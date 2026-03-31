"""
/research command handler — direct LLM path (no subprocess).

Loads knowledge base .md content from the export directory with query-aware
relevance scoring, builds a system prompt with KB context, calls the LLM
via _async_llm_completion, and saves the report to _reports/.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# KB content loader — pure function, no side effects
# ---------------------------------------------------------------------------

# Directories and files to exclude from KB content loading.
# These are filesystem artifacts from other subsystems, not knowledge content.
_KB_EXCLUDED_DIRS = frozenset({".opencode", "_reports", "inbox", ".git"})
_KB_EXCLUDED_FILES = frozenset({"opencode.json", ".webui_secret_key", "_catalog.md"})

# Default token budget (~32k tokens, roughly 128k chars at 4 chars/token)
DEFAULT_TOKEN_BUDGET = 32_000


def estimate_tokens(text: str) -> int:
    """Fast token estimation: character count / 4.

    This is a rough but fast approximation that avoids requiring tiktoken.
    For CJK-heavy text it may undercount slightly (CJK chars are ~1-2 tokens
    each but only 3 bytes in UTF-8), but for budget purposes it's adequate.
    """
    return len(text) // 4


# ---------------------------------------------------------------------------
# Query-aware relevance scoring — pure functions, no side effects
# ---------------------------------------------------------------------------

def _is_cjk_char(ch: str) -> bool:
    """Check if a character is a CJK ideograph (Chinese/Japanese/Korean).

    Uses Unicode category: Lo (Letter, other) characters in CJK Unified
    Ideographs ranges. This is faster than checking code point ranges and
    handles all CJK blocks including extensions.
    """
    cp = ord(ch)
    # CJK Unified Ideographs: U+4E00 to U+9FFF
    # CJK Extension A: U+3400 to U+4DBF
    # CJK Extension B+: U+20000 to U+2A6DF (rare, skip for speed)
    # CJK Compatibility Ideographs: U+F900 to U+FAFF
    return (0x4E00 <= cp <= 0x9FFF
            or 0x3400 <= cp <= 0x4DBF
            or 0xF900 <= cp <= 0xFAFF)


def tokenize_query(query: str) -> set[str]:
    """Tokenize a query into a set of searchable terms.

    For Latin/alphabetic text: split on whitespace, lowercase, strip punctuation.
    For CJK text: each CJK character is an individual token.
    Mixed queries produce both kinds of tokens.

    Returns a set (not a list) since we only need membership testing.
    Drops tokens shorter than 2 characters for Latin words (single letters
    are too noisy), but keeps all CJK characters (single chars are meaningful).

    Pure function: str in, frozenset out.
    """
    if not query or not query.strip():
        return set()

    tokens: set[str] = set()
    # Extract CJK characters as individual tokens
    for ch in query:
        if _is_cjk_char(ch):
            tokens.add(ch)

    # Extract Latin/alphabetic words: lowercase, strip non-alphanumeric
    words = re.findall(r"[a-zA-Z0-9]+", query.lower())
    for w in words:
        if len(w) >= 2:  # skip single letters — too noisy
            tokens.add(w)

    return tokens


def score_query_relevance(query_tokens: set[str], text: str, filename: str = "") -> float:
    """Score how relevant a text is to a set of query tokens.

    Returns a float in [0.0, 1.0]:
      score = (matching tokens) / (total query tokens)

    A token "matches" if it appears anywhere in the lowercased text or filename.
    For CJK tokens, this is character-level containment.
    For Latin tokens, this is substring containment in the lowercased text.

    Pure function: (set, str, str) -> float.
    """
    if not query_tokens:
        return 0.0

    searchable = (text + " " + filename).lower()
    matching = sum(1 for token in query_tokens if token in searchable)
    return matching / len(query_tokens)


def _should_include_path(path: Path, root: Path) -> bool:
    """Check if a path should be included in KB content loading.

    Excludes hidden directories/files, excluded dirs/files, and non-.md files.
    Note: calls path.is_file() for the non-.md extension check, so this is
    not fully pure — it performs a single stat() call on the path.
    """
    # Skip hidden files/dirs (except we check name, not full path)
    if path.name.startswith("."):
        return False

    if path.name in _KB_EXCLUDED_FILES:
        return False

    # Check if any ancestor (relative to root) is in excluded dirs
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False

    for part in rel.parts[:-1]:  # all parent components
        if part in _KB_EXCLUDED_DIRS or part.startswith("."):
            return False

    # Only .md files
    if path.is_file() and not path.name.endswith(".md"):
        return False

    return True


def _read_file_content(path: Path) -> Optional[str]:
    """Read file content, returning None on any I/O error.

    Tolerant of encoding issues — uses 'replace' error handler.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _classify_md_file(filename: str) -> str:
    """Classify a .md file as 'index' or 'full'.

    .index.md files are compact multi-dimensional indices.
    All other .md files are full document content.
    """
    return "index" if filename.endswith(".index.md") else "full"


def load_kb_content(
    kb_dir: str,
    *,
    query: Optional[str] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[dict]:
    """Load knowledge base content from a directory, respecting a token budget.

    Scans kb_dir recursively for .md and .index.md files.
    Prioritizes .index.md files (compact summaries) over .md files (full docs).
    If total content exceeds token_budget, index files are kept and full docs
    are truncated or skipped.

    When ``query`` is provided, full doc entries are sorted by relevance to the
    query (keyword overlap scoring) instead of alphabetically. This ensures the
    most relevant files are loaded first when the KB is larger than the budget.
    Index files are always loaded first in alphabetical order regardless.

    Args:
        kb_dir:       Path to the knowledge base root directory.
        query:        Optional research query for relevance-based ordering.
                      When None, full files are ordered alphabetically (legacy).
        token_budget: Maximum estimated tokens for all loaded content.

    Returns:
        List of dicts, each with keys:
          - filename:      The file's name (e.g., "doc.index.md")
          - relative_path: Path relative to kb_dir (e.g., "legal/contracts/doc.index.md")
          - content:       The file's text content (possibly truncated)
          - type:          "index" or "full"
          - tokens:        Estimated token count for this entry's content

    The list is sorted: all index files first (alphabetically by relative_path),
    then full files ordered by relevance (if query provided) or alphabetically.
    """
    root = Path(kb_dir)
    if not root.is_dir():
        return []

    # Phase 1: Collect all eligible .md files, classified and sorted
    index_entries: list[dict] = []
    full_entries: list[dict] = []

    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)

        # Prune excluded directories in-place (os.walk respects this)
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in _KB_EXCLUDED_DIRS and not d.startswith(".")
        ]

        for fname in sorted(filenames):
            fpath = current_path / fname
            if not _should_include_path(fpath, root):
                continue

            content = _read_file_content(fpath)
            if content is None or not content.strip():
                continue

            rel_path = fpath.relative_to(root).as_posix()
            file_type = _classify_md_file(fname)
            tokens = estimate_tokens(content)

            entry = {
                "filename": fname,
                "relative_path": rel_path,
                "content": content,
                "type": file_type,
                "tokens": tokens,
            }

            if file_type == "index":
                index_entries.append(entry)
            else:
                full_entries.append(entry)

    # Phase 1.5: If query provided, sort full entries by relevance (descending).
    # Index entries stay alphabetical — they're compact and always prioritized.
    if query:
        query_tokens = tokenize_query(query)
        if query_tokens:
            full_entries.sort(
                key=lambda e: score_query_relevance(
                    query_tokens, e["content"], e["filename"]
                ),
                reverse=True,
            )

    # Phase 2: Budget allocation — index files first, then full docs
    result: list[dict] = []
    tokens_used = 0

    # Minimum tokens to bother including an entry (~80 chars, one short paragraph)
    _MIN_MEANINGFUL_TOKENS = 20

    # Add all index entries (they're compact — prioritized)
    for entry in index_entries:
        if tokens_used + entry["tokens"] <= token_budget:
            result.append(entry)
            tokens_used += entry["tokens"]
        else:
            # Even index files get truncated if budget is very tight
            remaining_tokens = token_budget - tokens_used
            if remaining_tokens >= _MIN_MEANINGFUL_TOKENS:
                truncated_chars = remaining_tokens * 4
                truncated_content = entry["content"][:truncated_chars]
                result.append({
                    **entry,
                    "content": truncated_content,
                    "tokens": estimate_tokens(truncated_content),
                })
                tokens_used += estimate_tokens(truncated_content)
            break  # budget exhausted

    # Add full doc entries with remaining budget
    for entry in full_entries:
        remaining = token_budget - tokens_used
        if remaining < _MIN_MEANINGFUL_TOKENS:
            break  # no meaningful space left

        if entry["tokens"] <= remaining:
            result.append(entry)
            tokens_used += entry["tokens"]
        else:
            # Truncate this full doc to fit remaining budget
            truncated_chars = remaining * 4
            truncated_content = entry["content"][:truncated_chars]
            result.append({
                **entry,
                "content": truncated_content,
                "tokens": estimate_tokens(truncated_content),
            })
            tokens_used += estimate_tokens(truncated_content)
            break  # budget now exhausted

    return result


def format_kb_context(entries: list[dict]) -> str:
    """Format loaded KB entries into a text block suitable for an LLM prompt.

    Pure function: list of entry dicts in, formatted string out.
    """
    if not entries:
        return ""

    sections = []
    for entry in entries:
        header = f"--- {entry['relative_path']} ({entry['type']}) ---"
        sections.append(f"{header}\n{entry['content']}")

    return "\n\n".join(sections)

log = logging.getLogger(__name__)


RESEARCH_SYSTEM_PROMPT = """You are a research assistant. The knowledge base content is provided below.
Each section is a document from the knowledge base. Sections marked "(index)" are compact structured
indices; sections marked "(full)" are complete document content.

Your task:
1. Analyze the provided knowledge base content to find information relevant to the user's research query
2. Cross-reference information across multiple documents when applicable
3. Generate a comprehensive research report in markdown format

Report format:
- Start with a title and summary
- Organize findings by source document
- Include specific quotes, numbers, dates when relevant
- Note any connections or patterns across documents
- End with a brief conclusion

Be thorough but concise. Cite source filenames."""


async def run_research(
    query: str,
    kb_dir: str,
    app: Any,
    model: Optional[str] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    event_emitter=None,
) -> dict:
    """
    Research the knowledge base by loading content and calling the LLM directly.

    Args:
        query:        The research question
        kb_dir:       Path to the knowledge export directory
        app:          FastAPI application instance (carries state.MODELS, main_loop)
        model:        Model ID (e.g. "lmstudio.qwen3.5-35b-a3b"), None to read from config
        token_budget: Max estimated tokens for KB content (default: DEFAULT_TOKEN_BUDGET)
        event_emitter: Async event emitter for status updates

    Returns:
        dict with keys: report (str), report_path (str|None), success (bool), error (str|None)
    """
    if not os.path.isdir(kb_dir):
        return {
            "report": None,
            "report_path": None,
            "success": False,
            "error": f"Knowledge base directory not found: {kb_dir}",
        }

    if event_emitter:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "research",
                    "description": f"Researching: {query[:80]}...",
                    "done": False,
                },
            }
        )

    # --- Load KB content (query-aware: prioritizes relevant files) ---
    entries = load_kb_content(kb_dir, query=query, token_budget=token_budget)
    kb_context = format_kb_context(entries)

    if not kb_context:
        error_msg = "Knowledge base is empty or contains no .md files"
        log.warning(f"Research: {error_msg}")
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "research",
                        "description": error_msg,
                        "done": True,
                    },
                }
            )
        return {
            "report": None,
            "report_path": None,
            "success": False,
            "error": error_msg,
        }

    total_tokens = sum(e["tokens"] for e in entries)
    log.info(
        f"Research: loaded {len(entries)} KB entries "
        f"(~{total_tokens} tokens) for query: {query[:80]}"
    )

    # --- Resolve model ---
    if not model:
        config = getattr(getattr(app, "state", None), "config", None)
        model = getattr(config, "RAG_RESEARCH_MODEL", None) or ""
    if not model:
        error_msg = "No research model configured (RAG_RESEARCH_MODEL)"
        log.error(f"Research: {error_msg}")
        return {
            "report": None,
            "report_path": None,
            "success": False,
            "error": error_msg,
        }

    # --- Build messages ---
    system_content = f"{RESEARCH_SYSTEM_PROMPT}\n\n--- KNOWLEDGE BASE CONTENT ---\n\n{kb_context}"
    user_content = f"Research query: {query}"

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    # --- Call LLM ---
    from open_webui.utils.knowledge_export import _async_llm_completion

    try:
        report = await _async_llm_completion(app, messages, model)
    except Exception as e:
        log.error(f"Research LLM call failed: {e}")
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "research",
                        "description": f"Research failed: {str(e)[:80]}",
                        "done": True,
                    },
                }
            )
        return {
            "report": None,
            "report_path": None,
            "success": False,
            "error": str(e),
        }

    if not report or not report.strip():
        error_msg = "LLM returned empty report"
        log.error(f"Research: {error_msg}")
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "research",
                        "description": error_msg,
                        "done": True,
                    },
                }
            )
        return {
            "report": None,
            "report_path": None,
            "success": False,
            "error": error_msg,
        }

    # --- Save report ---
    report_path = _save_report(kb_dir, query, report)

    log.info(
        f"Research: report generated ({len(report)} chars), saved to {report_path}"
    )

    if event_emitter:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "research",
                    "description": f"Research complete ({len(report)} chars)",
                    "done": True,
                },
            }
        )

    return {
        "report": report,
        "report_path": report_path,
        "success": True,
        "error": None,
    }


def _save_report(kb_dir: str, query: str, report: str) -> str:
    """Save the research report to the kb dir's _reports/ subdirectory."""
    reports_dir = os.path.join(kb_dir, "_reports")
    os.makedirs(reports_dir, exist_ok=True)

    # Generate filename from query
    safe_query = "".join(c if c.isalnum() or c in " -_" else "_" for c in query[:50])
    safe_query = safe_query.strip().replace(" ", "_")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"research_{timestamp}_{safe_query}.md"

    report_path = os.path.join(reports_dir, filename)

    # Quote the query field to prevent YAML injection.
    # Escape backslashes first, then double quotes, then wrap in double quotes.
    # This handles queries containing colons, newlines, brackets, etc.
    escaped_query = query.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    frontmatter = (
        f"---\n"
        f"type: research_report\n"
        f'query: "{escaped_query}"\n'
        f"generated_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"---\n\n"
    )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(frontmatter + report)

    return report_path

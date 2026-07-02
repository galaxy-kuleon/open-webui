"""
skip-rag Phase 4 — query-aware extraction via a dedicated extractor model.

Public API
----------
async def extract_to_budget(
    items:         list[str],        # per-file markdown strings
    query:         str,              # last user message (extraction target)
    target_tokens: int,              # combined output must not exceed this
    request=None,                    # FastAPI Request (unused; kept for API compat)
) -> list[str]

Design
------
1.  Budget split: proportional to each item's token count, with a per-item
    floor (EXTRACTOR_FLOOR_TOKENS).  Files already under their share pass
    through untouched.

2.  For each over-budget item:
    a.  If the item is larger than EXTRACTOR_CHUNK_TOKENS, split into chunks
        (map step), extract per chunk, then concatenate (reduce).
    b.  Call the extractor model via direct async OpenAI-compatible HTTP
        (httpx), NOT through OWUI's generate_chat_completion — keeps the call
        self-contained and outside the middleware stack.
    c.  On any failure (network, timeout, empty response), fall back to the
        Phase-3 marker-aware truncation so we never hard-fail.

3.  Cache: keyed by (sha256(item_text), query_hash, target_tokens) under
    SKIP_RAG_CACHE_DIR/extract/.  Cache hit short-circuits the model call.

4.  Final guard: re-measure combined tokens; if still > target_tokens (e.g.
    extractor over-produced), truncate to fit.

Environment variables
---------------------
SKIP_RAG_EXTRACTOR_MODEL_ID     Model id to use for extraction
                                 (default: qwen3.5-122b-a10b-mtp)
SKIP_RAG_EXTRACTOR_API_BASE     OpenAI-compatible base URL
                                 (default: http://host.docker.internal:1234/v1)
SKIP_RAG_EXTRACTOR_API_KEY      Bearer key for the extractor endpoint
                                 (default: change-me-local-key)
SKIP_RAG_EXTRACTOR_MAX_TOKENS   max_tokens for each extractor call (default: 32768)
SKIP_RAG_EXTRACTOR_CHUNK_TOKENS Doc is split into this many tokens per chunk
                                 for the map step (default: 60000)
SKIP_RAG_EXTRACTOR_FLOOR_TOKENS Per-item minimum budget tokens (default: 1000)
SKIP_RAG_CACHE_DIR              Cache root (default: /app/backend/skiprag-cache)
TIKTOKEN_ENCODING_NAME          Encoding for token counting (default: cl100k_base)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
import tiktoken

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------

EXTRACTOR_MODEL_ID: str = os.environ.get(
    'SKIP_RAG_EXTRACTOR_MODEL_ID', 'qwen3.5-122b-a10b-mtp'
)
EXTRACTOR_API_BASE: str = os.environ.get(
    'SKIP_RAG_EXTRACTOR_API_BASE', 'http://host.docker.internal:1234/v1'
).rstrip('/')
EXTRACTOR_API_KEY: str = os.environ.get(
    'SKIP_RAG_EXTRACTOR_API_KEY', 'change-me-local-key'
)
EXTRACTOR_MAX_TOKENS: int = int(os.environ.get('SKIP_RAG_EXTRACTOR_MAX_TOKENS', '32768'))
EXTRACTOR_CHUNK_TOKENS: int = int(os.environ.get('SKIP_RAG_EXTRACTOR_CHUNK_TOKENS', '60000'))
EXTRACTOR_FLOOR_TOKENS: int = int(os.environ.get('SKIP_RAG_EXTRACTOR_FLOOR_TOKENS', '1000'))

CACHE_DIR: str = os.environ.get('SKIP_RAG_CACHE_DIR', '/app/backend/skiprag-cache')
TIKTOKEN_ENCODING: str = os.environ.get('TIKTOKEN_ENCODING_NAME', 'cl100k_base')

# Phase-3 truncation marker (fallback)
_TRUNCATION_MARKER: str = '\n\n[...truncated by skip-rag (extractor unavailable)...]\n'

# ---------------------------------------------------------------------------
# Token counting (local, no network)
# ---------------------------------------------------------------------------

_enc_cache: dict[str, tiktoken.Encoding] = {}


def _get_encoding() -> tiktoken.Encoding:
    if TIKTOKEN_ENCODING not in _enc_cache:
        _enc_cache[TIKTOKEN_ENCODING] = tiktoken.get_encoding(TIKTOKEN_ENCODING)
    return _enc_cache[TIKTOKEN_ENCODING]


def _count_tokens(text: str) -> int:
    try:
        return len(_get_encoding().encode(text))
    except Exception:
        return len(text) // 4


def _truncate_to_tokens(text: str, max_tok: int) -> str:
    """Hard-truncate text to at most max_tok tokens, returning decoded string."""
    enc = _get_encoding()
    tokens = enc.encode(text)
    if len(tokens) <= max_tok:
        return text
    return enc.decode(tokens[:max_tok])


# ---------------------------------------------------------------------------
# Extraction cache
# ---------------------------------------------------------------------------

def _extract_cache_dir() -> Path:
    p = Path(CACHE_DIR) / 'extract'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _extract_cache_key(item_text: str, query: str, target_tokens: int) -> str:
    h = hashlib.sha256(f'{item_text}\x00{query}\x00{target_tokens}'.encode()).hexdigest()
    return f'{h}.md'


def _cache_read(key: str) -> Optional[str]:
    path = _extract_cache_dir() / key
    if path.exists():
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            return None
    return None


def _cache_write(key: str, text: str) -> None:
    try:
        (_extract_cache_dir() / key).write_text(text, encoding='utf-8')
    except OSError as exc:
        log.warning('skip-rag[extract]: cache write failed: %s', exc)


# ---------------------------------------------------------------------------
# Extractor model call
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = (
    'You are a precise document-extraction assistant. '
    'Your task is to read a section of a document and extract ONLY the parts '
    'that are relevant to the user query. '
    'Preserve headings, tables, and structure. '
    'Output raw markdown only. '
    'Do NOT add commentary, preamble, or concluding remarks. '
    'If nothing is relevant, output exactly: [no relevant content]'
)

_EXTRACTION_USER_TMPL = (
    'USER QUERY:\n{query}\n\n'
    'DOCUMENT (excerpt):\n```markdown\n{doc}\n```\n\n'
    'Extract the parts of the document that are relevant to the query above. '
    'Stay within approximately {budget} tokens. '
    'Output markdown only.'
)


async def _call_extractor(doc: str, query: str, budget_tokens: int) -> Optional[str]:
    """
    Call the extractor model with (doc, query, budget) and return the
    extracted markdown.  Returns None on any error.

    Uses httpx directly against the OpenAI-compatible endpoint so we stay
    outside OWUI's generate_chat_completion middleware stack.
    """
    if not EXTRACTOR_MODEL_ID:
        log.warning('skip-rag[extract]: SKIP_RAG_EXTRACTOR_MODEL_ID is empty; cannot call extractor')
        return None

    user_prompt = _EXTRACTION_USER_TMPL.format(
        query=query,
        doc=doc,
        budget=budget_tokens,
    )

    payload = {
        'model': EXTRACTOR_MODEL_ID,
        'messages': [
            {'role': 'system', 'content': _EXTRACTION_SYSTEM},
            {'role': 'user', 'content': user_prompt},
        ],
        'stream': False,
        'max_tokens': EXTRACTOR_MAX_TOKENS,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f'{EXTRACTOR_API_BASE}/chat/completions',
                headers={
                    'Authorization': f'Bearer {EXTRACTOR_API_KEY}',
                    'Content-Type': 'application/json',
                },
                content=json.dumps(payload),
            )
            resp.raise_for_status()
            data = resp.json()
            content = data['choices'][0]['message'].get('content') or ''
            if not content:
                log.warning(
                    'skip-rag[extract]: extractor returned empty content '
                    '(model=%s, finish_reason=%s)',
                    EXTRACTOR_MODEL_ID,
                    data['choices'][0].get('finish_reason'),
                )
                return None
            log.info(
                'skip-rag[extract]: extractor call OK — model=%s, '
                'input_doc_chars=%d, output_chars=%d, '
                'finish_reason=%s',
                EXTRACTOR_MODEL_ID,
                len(doc),
                len(content),
                data['choices'][0].get('finish_reason'),
            )
            return content

    except httpx.HTTPStatusError as exc:
        log.warning(
            'skip-rag[extract]: extractor HTTP error %s for %s',
            exc.response.status_code, EXTRACTOR_API_BASE,
        )
        return None
    except httpx.TimeoutException:
        log.warning('skip-rag[extract]: extractor call timed out for model=%s', EXTRACTOR_MODEL_ID)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning('skip-rag[extract]: extractor call failed: %s', exc)
        return None


# ---------------------------------------------------------------------------
# Map-reduce chunking for oversized documents
# ---------------------------------------------------------------------------

async def _extract_with_chunks(item: str, query: str, budget_tokens: int) -> Optional[str]:
    """
    If item > EXTRACTOR_CHUNK_TOKENS, split into chunks and extract each,
    then concatenate.  This is a simple map (extract) step — no reduce
    summarisation, just concatenation of relevant passages.
    """
    enc = _get_encoding()
    all_tokens = enc.encode(item)
    total = len(all_tokens)

    if total <= EXTRACTOR_CHUNK_TOKENS:
        # Single chunk — call directly
        return await _call_extractor(item, query, budget_tokens)

    # Split into chunks
    chunks: list[str] = []
    start = 0
    while start < total:
        chunk_tokens = all_tokens[start: start + EXTRACTOR_CHUNK_TOKENS]
        chunks.append(enc.decode(chunk_tokens))
        start += EXTRACTOR_CHUNK_TOKENS

    log.info(
        'skip-rag[extract]: splitting %d-token doc into %d chunk(s) of ~%d tokens each',
        total, len(chunks), EXTRACTOR_CHUNK_TOKENS,
    )

    # Per-chunk budget: distribute evenly; each chunk contributes proportionally
    per_chunk_budget = max(256, budget_tokens // len(chunks))

    parts: list[str] = []
    for idx, chunk in enumerate(chunks):
        extracted = await _call_extractor(chunk, query, per_chunk_budget)
        if extracted and extracted.strip() not in ('[no relevant content]', ''):
            parts.append(extracted)
        else:
            log.info('skip-rag[extract]: chunk %d/%d yielded no relevant content', idx + 1, len(chunks))

    if not parts:
        return None
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Fallback: Phase-3 proportional truncation
# ---------------------------------------------------------------------------

def _truncation_fallback(items: list[str], target_tokens: int) -> list[str]:
    """
    Phase-3 stub logic: naive proportional truncation, marker-aware, combined ≤ target_tokens.
    Used when the extractor is unavailable or fails.
    """
    enc = _get_encoding()
    marker_tokens = len(enc.encode(_TRUNCATION_MARKER))
    total = sum(_count_tokens(item) for item in items)

    result: list[str] = []
    for item in items:
        item_tokens_count = _count_tokens(item)
        raw_budget = max(256, int(target_tokens * item_tokens_count / total)) if total > 0 else target_tokens
        effective_budget = max(1, raw_budget - marker_tokens)

        if item_tokens_count <= raw_budget:
            result.append(item)
        else:
            tokens = enc.encode(item)
            truncated = enc.decode(tokens[:effective_budget])
            result.append(truncated + _TRUNCATION_MARKER)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_to_budget(
    items: list[str],
    query: str,
    target_tokens: int,
    request=None,  # kept for API compatibility; unused here
) -> list[str]:
    """
    Phase 4 — query-aware extraction.

    For each item that exceeds its proportional share of target_tokens, call
    the extractor model to extract only the query-relevant passages.  Items
    already within their share pass through untouched.

    Parameters
    ----------
    items:
        List of per-file markdown strings.
    query:
        The current turn's user message — the extraction focus.
    target_tokens:
        Hard upper bound on combined output token count.
    request:
        Unused; kept so callers don't need to change signature.

    Returns
    -------
    list[str]
        Extracted (or passthrough) markdown, one per input item, with
        combined token count guaranteed ≤ target_tokens.
    """
    if not items:
        return items

    # --- Config validation ---
    if not EXTRACTOR_MODEL_ID:
        log.warning(
            'skip-rag[extract]: SKIP_RAG_EXTRACTOR_MODEL_ID not set; '
            'falling back to Phase-3 truncation for all items'
        )
        return _truncation_fallback(items, target_tokens)

    # --- Per-item budget split ---
    # Proportional to each item's token count; floor at EXTRACTOR_FLOOR_TOKENS.
    token_counts = [_count_tokens(item) for item in items]
    total_input_tokens = sum(token_counts)

    # We also need to account for the wrapping <source> markers in inject.py
    # (approximately 20 tokens each).  We conservatively subtract that from target.
    MARKER_OVERHEAD = 20 * len(items)
    effective_target = max(target_tokens - MARKER_OVERHEAD, target_tokens // 2)

    budgets: list[int] = []
    for tc in token_counts:
        if total_input_tokens > 0:
            share = int(effective_target * tc / total_input_tokens)
        else:
            share = effective_target // max(len(items), 1)
        budgets.append(max(EXTRACTOR_FLOOR_TOKENS, share))

    log.info(
        'skip-rag[extract]: total_input=%d tokens, target=%d, effective_target=%d, '
        'per-item budgets=%s',
        total_input_tokens, target_tokens, effective_target,
        [f'{b}' for b in budgets],
    )

    # --- Per-item extraction ---
    result: list[str] = []
    for idx, (item, tc, budget) in enumerate(zip(items, token_counts, budgets)):
        if tc <= budget:
            # Item already fits its share — pass through untouched
            log.info(
                'skip-rag[extract]: item %d/%d: %d tokens ≤ budget %d — passthrough',
                idx + 1, len(items), tc, budget,
            )
            result.append(item)
            continue

        # Check extraction cache first
        cache_key = _extract_cache_key(item, query, budget)
        cached = _cache_read(cache_key)
        if cached is not None:
            log.info(
                'skip-rag[extract]: item %d/%d: cache HIT (key=%s…)',
                idx + 1, len(items), cache_key[:16],
            )
            result.append(cached)
            continue

        log.info(
            'skip-rag[extract]: item %d/%d: %d tokens > budget %d — calling extractor model %s',
            idx + 1, len(items), tc, budget, EXTRACTOR_MODEL_ID,
        )

        extracted = await _extract_with_chunks(item, query, budget)

        if extracted and extracted.strip() not in ('[no relevant content]', ''):
            extracted_tok = _count_tokens(extracted)
            log.info(
                'skip-rag[extract]: item %d/%d: extractor produced %d tokens (budget was %d)',
                idx + 1, len(items), extracted_tok, budget,
            )
            # Per-item guard: if extractor over-produced, truncate to budget
            if extracted_tok > budget:
                log.warning(
                    'skip-rag[extract]: item %d/%d: extractor over-produced '
                    '(%d > %d); hard-truncating',
                    idx + 1, len(items), extracted_tok, budget,
                )
                extracted = _truncate_to_tokens(extracted, budget)

            _cache_write(cache_key, extracted)
            result.append(extracted)
        else:
            # Extractor failed or returned nothing — fall back to truncation for this item
            log.warning(
                'skip-rag[extract]: item %d/%d: extractor returned empty/no-content; '
                'falling back to proportional truncation',
                idx + 1, len(items),
            )
            # Single-item fallback: proportional truncation within item's budget
            enc = _get_encoding()
            marker_tok = len(enc.encode(_TRUNCATION_MARKER))
            effective_budget = max(1, budget - marker_tok)
            tokens = enc.encode(item)
            if len(tokens) <= budget:
                result.append(item)
            else:
                result.append(enc.decode(tokens[:effective_budget]) + _TRUNCATION_MARKER)

    # --- Final guard: combined ≤ target_tokens ---
    combined_tokens = sum(_count_tokens(r) for r in result)
    if combined_tokens > target_tokens:
        log.warning(
            'skip-rag[extract]: combined output %d tokens still > target %d; '
            'applying final proportional truncation guard',
            combined_tokens, target_tokens,
        )
        result = _truncation_fallback(result, target_tokens)
        final_tokens = sum(_count_tokens(r) for r in result)
        log.info(
            'skip-rag[extract]: after final guard: combined=%d tokens (target=%d)',
            final_tokens, target_tokens,
        )
    else:
        log.info(
            'skip-rag[extract]: combined output %d tokens ≤ target %d — OK',
            combined_tokens, target_tokens,
        )

    return result

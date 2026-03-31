"""
Knowledge Export System

Exports processed markdown files (.md + .index.md) to a filesystem directory
and uses an LLM (via OpenWebUI's internal generate_chat_completion) to choose
destination directories for them.
The backend then performs exact filesystem moves in Python, which keeps
weaker models away from brittle filename and shell-state handling.

Features:
- Export hook: writes .md and .index.md after document processing
- Background organizer: asks LLM for a directory plan via internal API
- Exact file moves happen in Python, not in model-written shell commands
- Job queue with threading to avoid blocking the frontend
"""

import asyncio
import json
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Background job queue for organization tasks
_org_queue: queue.Queue = queue.Queue()
_org_worker: Optional[threading.Thread] = None
_org_worker_lock = threading.Lock()

# Filesystem artifacts that may appear in the export directory from other
# subsystems (e.g., research utils, agent tooling).  Exclude them from the
# knowledge-base catalog so they don't pollute the directory tree listing.
CATALOG_EXCLUDED_DIRS = {".opencode", "_reports"}
CATALOG_EXCLUDED_FILES = {"opencode.json", ".webui_secret_key"}
ORGANIZER_BATCH_SIZE = 12

ORGANIZE_PLANNER_PROMPT = """You are a knowledge-base filing planner.
Return JSON only. Do not wrap the JSON in markdown fences.
Do not use tools. Do not ask to inspect the filesystem. All required context is included below.
For each doc_id, choose only a destination directory. The application will perform the file moves.

Rules:
- Reuse existing directories when appropriate.
- Destination paths must be relative to the knowledge-base root.
- Use 1-4 path components.
- Each path component must use lowercase letters, numbers, and hyphens only.
- NEVER use spaces in path components. Use hyphens instead.
- Do not include filenames in the destination.
- Keep each document's .md and .index.md files together.
- Prefer a reasonable broad category over an overly specific new directory.
- Every doc_id must appear exactly once.

Return exactly this JSON shape:
{"moves":[{"doc_id":"doc-0001","destination":"healthcare/accreditation","reason":"short reason"}]}
"""


async def _async_llm_completion(
    app: Any,
    messages: list[dict],
    model_id: str,
) -> str:
    """
    Async inner function: build a synthetic Request, fetch an admin user,
    call generate_chat_completion (non-streaming), and return the response text.

    Runs on the main event loop — never call this directly from a sync thread.
    """
    from starlette.datastructures import Headers
    from starlette.requests import Request

    from open_webui.models.users import Users
    from open_webui.utils.chat import generate_chat_completion

    # Get an admin user for authorization.
    # The organizer is a system-level task, not user-initiated.
    admin_user = Users.get_super_admin_user()
    if admin_user is None:
        admin_user = Users.get_first_user()
    if admin_user is None:
        raise RuntimeError("No admin user available for LLM completion")

    # Synthetic Request — same pattern as main.py startup (mock_request).
    # generate_chat_completion reads request.app.state.MODELS and request.state.
    request = Request(
        {
            "type": "http",
            "asgi.version": "3.0",
            "asgi.spec_version": "2.0",
            "method": "POST",
            "path": "/internal/knowledge-organizer",
            "query_string": b"",
            "headers": Headers({}).raw,
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 80),
            "scheme": "http",
            "app": app,
        }
    )

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "metadata": {"task": "knowledge_organizer"},
    }

    response = await generate_chat_completion(
        request, form_data=payload, user=admin_user, bypass_filter=True
    )

    # Response is either a dict (most common for stream=False) or a
    # StreamingResponse/JSONResponse. Handle both following the pattern
    # from middleware.py extract_relevant_content_from_document.
    if isinstance(response, dict) and "choices" in response:
        content = (
            response["choices"][0].get("message", {}).get("content", "")
            if response["choices"]
            else ""
        )
    elif hasattr(response, "body_iterator"):
        # StreamingResponse — drain it and extract content
        content = None
        async for chunk in response.body_iterator:
            data = json.loads(chunk.decode("utf-8", "replace"))
            if "choices" in data and data["choices"]:
                content = data["choices"][0].get("message", {}).get("content")
        if hasattr(response, "background") and response.background is not None:
            await response.background()
        content = content or ""
    elif hasattr(response, "body"):
        # JSONResponse — parse the body
        data = json.loads(response.body.decode("utf-8", "replace"))
        content = (
            data["choices"][0].get("message", {}).get("content", "")
            if data.get("choices")
            else ""
        )
    else:
        raise RuntimeError(f"Unexpected response type from LLM: {type(response)}")

    if not content:
        raise RuntimeError("LLM returned empty content")

    return content


def call_llm_completion(
    app: Any,
    system_prompt: str,
    user_prompt: str,
    model_id: str,
    timeout: float = 300.0,
) -> str:
    """
    Synchronous bridge: call generate_chat_completion from a non-async thread.

    Uses asyncio.run_coroutine_threadsafe to schedule the async call on the
    main event loop (app.state.main_loop), then blocks until the result is
    ready or the timeout expires.

    Args:
        app:           The FastAPI application instance (carries state.MODELS, etc.)
        system_prompt: System message content
        user_prompt:   User message content
        model_id:      OpenWebUI model ID (e.g. "lmstudio.qwen3.5-35b-a3b")
        timeout:       Maximum seconds to wait for the LLM response

    Returns:
        The LLM response text (str).

    Raises:
        RuntimeError: If the event loop is unavailable, the LLM fails, or timeout.
        TimeoutError: If the LLM call exceeds the timeout.
    """
    loop = getattr(getattr(app, "state", None), "main_loop", None)
    if loop is None or loop.is_closed():
        raise RuntimeError(
            "Main event loop not available (app.state.main_loop). "
            "Server may still be starting up."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    future = asyncio.run_coroutine_threadsafe(
        _async_llm_completion(app, messages, model_id),
        loop,
    )

    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise TimeoutError(
            f"LLM completion timed out after {timeout}s (model={model_id})"
        )
    except Exception:
        # Re-raise the original exception from the async side
        raise


def _sanitize_filename(name: str) -> str:
    """Sanitize filename for safe filesystem use and LLM tool-call compatibility.

    Keeps: CJK chars, alphanumeric, hyphen, underscore, dot.
    Replaces: spaces, parens, commas, punctuation -> hyphen.
    Collapses consecutive hyphens/underscores.
    """
    stem = Path(name).stem
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem)
    sanitized = re.sub(
        r"[\s()\[\]{}【】「」『』〈〉《》,;、，；！!@#$%^&+=~`']+", "-", sanitized
    )
    sanitized = re.sub(r"-+", "-", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = sanitized.strip("-_")
    return sanitized or "unnamed"


def _strip_yaml_frontmatter(text: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n?", "", text, count=1, flags=re.DOTALL)


def _read_path_text(path: Optional[Path]) -> str:
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_frontmatter_value(path: Optional[Path], key: str) -> str:
    text = _read_path_text(path)
    if not text:
        return ""
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _read_preview(
    path: Optional[Path],
    *,
    max_lines: int = 14,
    max_chars: int = 2200,
) -> str:
    text = _strip_yaml_frontmatter(_read_path_text(path)).strip()
    if not text:
        return ""

    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    preview = "\n".join(lines[:max_lines]).strip()
    if len(preview) > max_chars:
        preview = preview[:max_chars].rstrip()
    if len(preview) < len(text):
        preview = preview.rstrip() + "..."
    return preview


def _collect_inbox_documents(inbox_dir: str) -> list[dict]:
    grouped: dict[str, dict] = {}

    for path in sorted(Path(inbox_dir).iterdir(), key=lambda item: item.name):
        if not path.is_file() or not path.name.endswith(".md"):
            continue

        if path.name.endswith(".index.md"):
            base_name = path.name[:-9]
            entry = grouped.setdefault(
                base_name,
                {"base_name": base_name, "md_path": None, "index_path": None},
            )
            entry["index_path"] = path
        else:
            base_name = path.name[:-3]
            entry = grouped.setdefault(
                base_name,
                {"base_name": base_name, "md_path": None, "index_path": None},
            )
            entry["md_path"] = path

    documents = []
    for index, base_name in enumerate(sorted(grouped), start=1):
        entry = grouped[base_name]
        primary_path = entry["md_path"] or entry["index_path"]
        original_filename = (
            _read_frontmatter_value(primary_path, "original_filename") or base_name
        )
        documents.append(
            {
                "doc_id": f"doc-{index:04d}",
                "base_name": base_name,
                "md_path": entry["md_path"],
                "index_path": entry["index_path"],
                "original_filename": original_filename,
                "content_preview": _read_preview(
                    entry["md_path"], max_lines=12, max_chars=1400
                ),
                "index_preview": _read_preview(
                    entry["index_path"], max_lines=16, max_chars=2200
                ),
            }
        )

    return documents


def _summarize_existing_directories(export_dir: str) -> list[dict]:
    root = Path(export_dir)
    summary = []

    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_path = current_path.relative_to(root).as_posix()

        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in CATALOG_EXCLUDED_DIRS
            and name != "inbox"
            and not name.startswith(".")
        )

        if rel_path == ".":
            continue

        markdown_files = sorted(
            name for name in filenames if name.endswith(".md") and name != "_catalog.md"
        )
        if markdown_files or dirnames:
            summary.append(
                {
                    "path": rel_path,
                    "direct_markdown_files": len(markdown_files),
                    "sample_files": markdown_files[:3],
                    "subdirectories": dirnames[:5],
                }
            )

    return summary[:80]


def _build_organizer_context(export_dir: str, documents: list[dict]) -> dict:
    return {
        "existing_directories": _summarize_existing_directories(export_dir),
        "documents": [
            {
                "doc_id": document["doc_id"],
                "safe_base_name": document["base_name"],
                "original_filename": document["original_filename"],
                "has_content_file": bool(document["md_path"]),
                "has_index_file": bool(document["index_path"]),
                "index_preview": document["index_preview"],
                "content_preview": document["content_preview"],
            }
            for document in documents
        ],
    }


def _build_organizer_prompt(export_dir: str, documents: list[dict]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the organizer planner LLM call.

    system_prompt = the ORGANIZE_PLANNER_PROMPT instructions
    user_prompt   = the context JSON describing documents and existing directories
    """
    context_json = json.dumps(
        _build_organizer_context(export_dir, documents),
        ensure_ascii=False,
        indent=2,
    )
    return ORGANIZE_PLANNER_PROMPT, f"Context JSON:\n{context_json}"


def _extract_json_payload(text: str):
    if not text:
        return None

    stripped = text.strip()
    candidates = []

    fenced_blocks = re.findall(
        r"```(?:json)?\s*(.*?)```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    candidates.extend(block.strip() for block in fenced_blocks if block.strip())

    if stripped:
        candidates.append(stripped)

        object_start = stripped.find("{")
        object_end = stripped.rfind("}")
        if object_start != -1 and object_end != -1 and object_end > object_start:
            candidates.append(stripped[object_start : object_end + 1].strip())

        array_start = stripped.find("[")
        array_end = stripped.rfind("]")
        if array_start != -1 and array_end != -1 and array_end > array_start:
            candidates.append(stripped[array_start : array_end + 1].strip())

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def _sanitize_destination_path(destination: str) -> str:
    if not destination:
        return ""

    parts = []
    for raw_part in destination.replace("\\", "/").split("/"):
        raw_part = raw_part.strip()
        if not raw_part:
            continue

        lowered = raw_part.lower()
        if lowered in {".", "..", "inbox", "_reports"}:
            return ""

        normalized = lowered.replace("_", "-")
        normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
        normalized = re.sub(r"-+", "-", normalized).strip("-")
        if not normalized:
            return ""

        parts.append(normalized)

    if not parts:
        return ""

    return "/".join(parts[:4])


def _parse_organizer_plan(output: str, documents: list[dict]) -> dict:
    payload = _extract_json_payload(output)
    if isinstance(payload, dict):
        moves = payload.get("moves", [])
    elif isinstance(payload, list):
        moves = payload
    else:
        raise ValueError("Organizer planner did not return JSON")

    valid_ids = {document["doc_id"] for document in documents}
    plan = {}

    for move in moves:
        if not isinstance(move, dict):
            continue

        doc_id = str(move.get("doc_id", "")).strip()
        if not doc_id or doc_id not in valid_ids:
            continue
        if doc_id in plan:
            raise ValueError(f"Duplicate planner entry for {doc_id}")

        destination = _sanitize_destination_path(
            str(move.get("destination", "")).strip()
        )
        if not destination:
            raise ValueError(
                f"Invalid destination for {doc_id}: {move.get('destination')!r}"
            )

        plan[doc_id] = {
            "destination": destination,
            "reason": str(move.get("reason", "")).strip(),
        }

    missing = sorted(valid_ids - set(plan))
    if missing:
        raise ValueError(f"Organizer planner missed document ids: {', '.join(missing)}")

    return plan


def _choose_available_base_name(
    dest_dir: Path,
    base_name: str,
    *,
    has_md: bool,
    has_index: bool,
    reserved_paths: set[str],
) -> str:
    candidate = base_name
    counter = 2

    while True:
        targets = []
        if has_md:
            targets.append(dest_dir / f"{candidate}.md")
        if has_index:
            targets.append(dest_dir / f"{candidate}.index.md")

        if all(
            str(target) not in reserved_paths and not target.exists()
            for target in targets
        ):
            for target in targets:
                reserved_paths.add(str(target))
            return candidate

        candidate = f"{base_name}-{counter}"
        counter += 1


def _build_move_plan(export_dir: str, documents: list[dict], plan: dict) -> list[dict]:
    root = Path(export_dir)
    reserved_paths: set[str] = set()
    move_plan = []

    for document in documents:
        destination = plan[document["doc_id"]]["destination"]
        dest_dir = root / destination
        final_base_name = _choose_available_base_name(
            dest_dir,
            document["base_name"],
            has_md=bool(document["md_path"]),
            has_index=bool(document["index_path"]),
            reserved_paths=reserved_paths,
        )

        move_plan.append(
            {
                "doc_id": document["doc_id"],
                "original_filename": document["original_filename"],
                "reason": plan[document["doc_id"]]["reason"],
                "destination": destination,
                "final_base_name": final_base_name,
                "md_source": document["md_path"],
                "index_source": document["index_path"],
                "md_target": dest_dir / f"{final_base_name}.md"
                if document["md_path"]
                else None,
                "index_target": dest_dir / f"{final_base_name}.index.md"
                if document["index_path"]
                else None,
            }
        )

    return move_plan


def _should_include_in_catalog(path: Path, root: Path) -> bool:
    rel_path = path.relative_to(root).as_posix()

    if path.name.startswith("."):
        return False
    if path.name in CATALOG_EXCLUDED_FILES:
        return False
    if any(
        rel_path == prefix or rel_path.startswith(f"{prefix}/")
        for prefix in CATALOG_EXCLUDED_DIRS
    ):
        return False
    if path.is_file() and not path.name.endswith(".md"):
        return False

    return True


def _visible_catalog_children(path: Path, root: Path) -> list[Path]:
    return [
        child
        for child in sorted(
            path.iterdir(),
            key=lambda item: (
                item.name != "_catalog.md",
                not item.is_dir(),
                item.name.lower(),
            ),
        )
        if _should_include_in_catalog(child, root)
    ]


def _build_catalog_tree(root: Path, path: Path, prefix: str = "") -> list[str]:
    lines = []
    children = _visible_catalog_children(path, root)

    for index, child in enumerate(children):
        connector = "└── " if index == len(children) - 1 else "├── "
        label = f"{child.name}/" if child.is_dir() else child.name
        lines.append(f"{prefix}{connector}{label}")
        if child.is_dir():
            extension = "    " if index == len(children) - 1 else "│   "
            lines.extend(_build_catalog_tree(root, child, prefix + extension))

    return lines


def _catalog_file_count(root: Path, dir_path: Path) -> int:
    return sum(
        1
        for child in dir_path.iterdir()
        if child.is_file()
        and child.name.endswith(".md")
        and _should_include_in_catalog(child, root)
        and child.name != "_catalog.md"
    )


def _write_catalog(export_dir: str, move_plan: list[dict]) -> None:
    root = Path(export_dir)
    catalog_path = root / "_catalog.md"
    catalog_path.touch(exist_ok=True)

    tree_lines = _build_catalog_tree(root, root)
    count_lines = []

    for current_root, dirnames, _ in os.walk(root):
        current_path = Path(current_root)
        rel_path = current_path.relative_to(root).as_posix()

        dirnames[:] = sorted(
            name
            for name in dirnames
            if _should_include_in_catalog(current_path / name, root)
        )

        if rel_path == "." or not _should_include_in_catalog(current_path, root):
            continue

        file_count = _catalog_file_count(root, current_path)
        if file_count or rel_path == "inbox":
            count_lines.append(f"- `{rel_path}`: {file_count} files")

    lines = [
        "# Knowledge Base Catalog",
        "",
        f"**Last Updated:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Directory Structure",
        "",
        "```",
        *(tree_lines or ["(empty)"]),
        "```",
        "",
        "## File Counts by Directory",
        "",
        *(count_lines or ["- `inbox`: 0 files"]),
    ]

    if move_plan:
        change_timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines.extend(["", "## Recent Changes", ""])
        for item in move_plan:
            lines.append(
                f"- **{change_timestamp}**: `{item['original_filename']}` -> `{item['destination']}/`"
            )

    catalog_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_document_files(
    export_dir: str,
    file_id: str,
    filename: str,
    content: str,
    index_content: Optional[str] = None,
) -> dict:
    """
    Export a processed document's .md and .index.md to the export directory.
    Files are placed in an 'inbox/' subdirectory for the organizer to sort.

    Returns dict with exported file paths.
    """
    if not export_dir or not content:
        return {}

    inbox_dir = os.path.join(export_dir, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)

    safe_name = _sanitize_filename(filename)
    result = {}

    md_path = os.path.join(inbox_dir, f"{safe_name}.md")
    try:
        frontmatter = (
            f"---\n"
            f"source_file_id: {file_id}\n"
            f"original_filename: {filename}\n"
            f"exported_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"---\n\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(frontmatter + content)
        result["md_path"] = md_path
        log.info(f"Knowledge export: wrote {md_path} ({len(content)} chars)")
    except Exception as e:
        log.error(f"Knowledge export: failed to write {md_path}: {e}")

    if index_content:
        index_path = os.path.join(inbox_dir, f"{safe_name}.index.md")
        try:
            frontmatter = (
                f"---\n"
                f"source_file_id: {file_id}\n"
                f"original_filename: {filename}\n"
                f"type: index\n"
                f"exported_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                f"---\n\n"
            )
            with open(index_path, "w", encoding="utf-8") as f:
                f.write(frontmatter + index_content)
            result["index_path"] = index_path
            log.info(
                f"Knowledge export: wrote {index_path} ({len(index_content)} chars)"
            )
        except Exception as e:
            log.error(f"Knowledge export: failed to write {index_path}: {e}")

    return result


def enqueue_organization(app: Any):
    """
    Enqueue a background organization job.
    The LLM will be asked for directory choices via the internal API bridge,
    then Python will move the exact files into topic directories.

    All configuration (export_dir, model) is read from app.state.config
    inside the worker, so callers just pass the app instance.

    Deduplicates: if a job is already queued, the new request is skipped
    to avoid redundant LLM calls.
    """
    if not _org_queue.empty():
        log.info("Knowledge organizer: job already queued, skipping")
        return

    _org_queue.put(
        {
            "app": app,
            "enqueued_at": time.time(),
        }
    )
    _ensure_worker_running()


def _ensure_worker_running():
    """Start the background organization worker if not already running."""
    global _org_worker
    with _org_worker_lock:
        if _org_worker is None or not _org_worker.is_alive():
            _org_worker = threading.Thread(
                target=_organization_worker,
                daemon=True,
                name="knowledge-organizer",
            )
            _org_worker.start()
            log.info("Knowledge organizer worker started")


def _organization_worker():
    """
    Background worker that processes organization jobs from the queue.
    Runs as a daemon thread - won't block server shutdown.
    """
    log.info("Knowledge organizer worker running")
    while True:
        try:
            job = _org_queue.get(timeout=60)
        except queue.Empty:
            log.info("Knowledge organizer worker idle, exiting")
            return

        try:
            _organize_inbox(app=job["app"])
        except Exception as e:
            log.error(f"Knowledge organizer job failed: {e}")
        finally:
            _org_queue.task_done()


def _run_organizer_planner(
    app: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float = 300.0,
) -> str:
    """Call the LLM via the internal bridge to get an organization plan.

    Uses call_llm_completion() which schedules the async generate_chat_completion
    on the main event loop from this background worker thread.

    Args:
        app:           FastAPI application instance (carries state.MODELS, main_loop)
        model:         OpenWebUI model ID for the organizer LLM
        system_prompt: ORGANIZE_PLANNER_PROMPT instructions
        user_prompt:   Context JSON describing documents and existing directories
        timeout:       Maximum seconds to wait for the LLM response

    Returns:
        The raw LLM response text (expected to contain JSON).
    """
    return call_llm_completion(
        app=app,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model_id=model,
        timeout=timeout,
    )


def _organize_inbox(
    app: Any,
    timeout: float = 300.0,
):
    """
    Ask the LLM for directory destinations via the internal API bridge,
    then move the exact inbox files in Python so weak models never have to
    spell or move CJK filenames themselves.

    Reads export_dir and model from app.state.config:
      - RAG_KNOWLEDGE_EXPORT_DIR
      - RAG_KNOWLEDGE_ORGANIZER_MODEL
    """
    config = getattr(getattr(app, "state", None), "config", None)
    if config is None:
        raise RuntimeError("app.state.config is not available")

    export_dir = getattr(config, "RAG_KNOWLEDGE_EXPORT_DIR", "") or ""
    if not export_dir:
        log.warning("Knowledge organizer: RAG_KNOWLEDGE_EXPORT_DIR is not configured, skipping")
        return

    model = getattr(config, "RAG_KNOWLEDGE_ORGANIZER_MODEL", "") or ""
    if not model:
        log.warning("Knowledge organizer: RAG_KNOWLEDGE_ORGANIZER_MODEL is not configured, skipping")
        return

    inbox_dir = os.path.join(export_dir, "inbox")
    if not os.path.isdir(inbox_dir):
        return

    documents = _collect_inbox_documents(inbox_dir)
    if not documents:
        log.info("Knowledge organizer: inbox is empty, nothing to organize")
        return

    file_list = ", ".join(document["base_name"] for document in documents)
    log.info(
        "Knowledge organizer: requesting destination plan for "
        f"{len(documents)} document(s): {file_list}"
    )

    try:
        while documents:
            batch = documents[:ORGANIZER_BATCH_SIZE]
            system_prompt, user_prompt = _build_organizer_prompt(export_dir, batch)
            planner_text = _run_organizer_planner(
                app=app,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=timeout,
            )
            plan = _parse_organizer_plan(planner_text, batch)
            move_plan = _build_move_plan(export_dir, batch, plan)

            moved_items = []
            for item in move_plan:
                targets = [
                    target
                    for target in (item["md_target"], item["index_target"])
                    if target
                ]
                if not targets:
                    continue

                targets[0].parent.mkdir(parents=True, exist_ok=True)
                if item["md_source"] and item["md_target"]:
                    item["md_source"].rename(item["md_target"])
                if item["index_source"] and item["index_target"]:
                    item["index_source"].rename(item["index_target"])

                moved_items.append(item)
                log.info(
                    f"Knowledge organizer: moved {item['doc_id']} to {item['destination']}"
                )

            _write_catalog(export_dir, moved_items)
            documents = _collect_inbox_documents(inbox_dir)

        log.info("Knowledge organizer: organization plan applied successfully")
    except Exception as e:
        log.error(f"Knowledge organizer: unexpected error: {e}")

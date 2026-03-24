"""
Knowledge Export System

Exports processed markdown files (.md + .index.md) to a filesystem directory
and uses OpenCode to choose destination directories for them.
The backend then performs exact filesystem moves in Python, which keeps
weaker models away from brittle filename and shell-state handling.

Features:
- Export hook: writes .md and .index.md after document processing
- Background organizer: asks OpenCode for a directory plan
- Exact file moves happen in Python, not in model-written shell commands
- Job queue with threading to avoid blocking the frontend
"""

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Background job queue for organization tasks
_org_queue: queue.Queue = queue.Queue()
_org_worker: Optional[threading.Thread] = None
_org_worker_lock = threading.Lock()

# Track active subprocesses for cleanup on shutdown
_active_processes: set[subprocess.Popen] = set()
_active_processes_lock = threading.Lock()

CATALOG_EXCLUDED_DIRS = {".opencode", "_reports"}
CATALOG_EXCLUDED_FILES = {"opencode.json", ".webui_secret_key"}
ORGANIZER_PERMISSION_CONFIG = {"*": "deny"}
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
- Do not include filenames in the destination.
- Keep each document's .md and .index.md files together.
- Prefer a reasonable broad category over an overly specific new directory.
- Every doc_id must appear exactly once.

Return exactly this JSON shape:
{"moves":[{"doc_id":"doc-0001","destination":"healthcare/accreditation","reason":"short reason"}]}
"""


def kill_all_organizer_processes():
    """Kill all tracked knowledge-organizer subprocesses. Called on server shutdown."""
    import signal

    with _active_processes_lock:
        if not _active_processes:
            return
        log.info(
            f"Shutting down: killing {len(_active_processes)} organizer subprocess(es)"
        )
        for proc in list(_active_processes):
            try:
                # Kill entire process group (node + .opencode child)
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                log.info(f"Killed organizer process group PID {proc.pid}")
            except (ProcessLookupError, OSError):
                # Fallback: try killing just the process
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        _active_processes.clear()


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


def _build_organizer_prompt(export_dir: str, documents: list[dict]) -> str:
    context_json = json.dumps(
        _build_organizer_context(export_dir, documents),
        ensure_ascii=False,
        indent=2,
    )
    return f"{ORGANIZE_PLANNER_PROMPT}\nContext JSON:\n{context_json}\n"


def _extract_text_from_opencode_output(output: str) -> str:
    if not output:
        return ""

    text_chunks = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict):
            continue

        event_type = event.get("type", "")

        if event_type == "text" and isinstance(event.get("part"), dict):
            part = event["part"]
            text = part.get("text", "") or part.get("content", "")
            if text:
                text_chunks.append(text)
                continue

        if event_type in ("text", "content"):
            text = event.get("content", "") or event.get("text", "")
            if text:
                text_chunks.append(text)
                continue

        if event_type == "result":
            text = event.get("result", "") or event.get("content", "")
            if text:
                text_chunks.append(text)
                continue

        if event.get("role") == "assistant" and event.get("content"):
            text_chunks.append(event["content"])
            continue

        if isinstance(event.get("message"), dict):
            message = event["message"]
            if message.get("role") == "assistant" and message.get("content"):
                text_chunks.append(message["content"])

    return "".join(text_chunks)


def _extract_opencode_error(output: str) -> str:
    if not output:
        return ""

    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(event, dict) or event.get("type") != "error":
            continue

        error = event.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict) and data.get("message"):
                return str(data["message"])
            if error.get("message"):
                return str(error["message"])

        if error:
            return str(error)

    return ""


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


def enqueue_organization(
    export_dir: str,
    opencode_path: str = "opencode",
    model: str = "",
):
    """
    Enqueue a background organization job.
    OpenCode will be asked for directory choices, then Python will move the
    exact files into topic directories.

    Deduplicates: if a job for the same export_dir is already queued or running,
    the new request is skipped to avoid spawning redundant opencode processes.
    """
    if not _org_queue.empty():
        log.info("Knowledge organizer: job already queued, skipping")
        return
    with _active_processes_lock:
        if _active_processes:
            log.info("Knowledge organizer: opencode already running, skipping")
            return

    _org_queue.put(
        {
            "export_dir": export_dir,
            "opencode_path": opencode_path,
            "model": model,
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
            _organize_with_opencode(
                export_dir=job["export_dir"],
                opencode_path=job.get("opencode_path", "opencode"),
                model=job.get("model", ""),
            )
        except Exception as e:
            log.error(f"Knowledge organizer job failed: {e}")
        finally:
            _org_queue.task_done()


def _run_organizer_planner(
    export_dir: str,
    opencode_path: str,
    model: str,
    prompt: str,
    idle_timeout: int,
) -> str:
    cmd = [
        opencode_path,
        "run",
        "--format",
        "json",
        "--dir",
        export_dir,
    ]
    if model:
        opencode_model = model.replace(".", "/", 1) if "." in model else model
        cmd.extend(["--model", opencode_model])
    cmd.append(prompt)

    env = os.environ.copy()
    for key in [
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "PYTHONHOME",
        "PYTHONPATH",
        "UV_PROJECT_ENVIRONMENT",
    ]:
        env.pop(key, None)

    from open_webui.utils.opencode import sync_opencode_config_to_dir

    sync_opencode_config_to_dir(export_dir)

    planner_config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": ORGANIZER_PERMISSION_CONFIG,
    }
    config_path = Path(export_dir) / "opencode.json"
    if config_path.exists():
        try:
            planner_config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Knowledge organizer: failed to read opencode config: {e}")
    planner_config.setdefault("$schema", "https://opencode.ai/config.json")
    planner_config["permission"] = ORGANIZER_PERMISSION_CONFIG
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(planner_config)

    proc = subprocess.Popen(
        cmd,
        cwd=export_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    with _active_processes_lock:
        _active_processes.add(proc)

    activity = {"last": time.monotonic()}
    stdout_chunks = []
    stderr_chunks = []

    def _drain(stream, dest, label):
        for line in iter(stream.readline, b""):
            activity["last"] = time.monotonic()
            text = line.decode("utf-8", errors="replace")
            dest.append(text)
            preview = (
                text.rstrip()[:2500] + " ... " + text.rstrip()[-2500:]
                if len(text.rstrip()) > 5000
                else text.rstrip()
            )
            log.info(f"[organizer:{label}] {preview}")
        stream.close()

    t_out = threading.Thread(
        target=_drain,
        args=(proc.stdout, stdout_chunks, "stdout"),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_drain,
        args=(proc.stderr, stderr_chunks, "stderr"),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    try:
        while proc.poll() is None:
            time.sleep(10)
            idle_secs = time.monotonic() - activity["last"]
            if idle_secs >= idle_timeout:
                import signal

                log.error(
                    "Knowledge organizer: OpenCode idle-timed out "
                    f"(no output for {int(idle_secs)}s), killing process group"
                )
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                proc.wait()
                raise TimeoutError(f"Knowledge organizer idle for {int(idle_secs)}s")

        stdout_all = "".join(stdout_chunks)
        stderr_all = "".join(stderr_chunks)
        planner_error = _extract_opencode_error(stdout_all) or _extract_opencode_error(
            stderr_all
        )

        if planner_error:
            raise RuntimeError(planner_error)

        if proc.returncode != 0:
            raise RuntimeError(
                f"OpenCode exited with code {proc.returncode}: {(stderr_all or stdout_all)[:500]}"
            )

        text_output = _extract_text_from_opencode_output(stdout_all)
        if not text_output:
            raise RuntimeError(
                f"OpenCode returned no planner text: {(stdout_all or stderr_all)[:500]}"
            )

        return text_output
    finally:
        t_out.join(timeout=10)
        t_err.join(timeout=10)
        with _active_processes_lock:
            _active_processes.discard(proc)


def _organize_with_opencode(
    export_dir: str,
    opencode_path: str = "opencode",
    model: str = "",
    idle_timeout: int = 600,
):
    """
    Ask OpenCode for directory destinations, then move the exact inbox files in
    Python so weak models never have to spell or move CJK filenames themselves.
    """
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
            planner_text = _run_organizer_planner(
                export_dir=export_dir,
                opencode_path=opencode_path,
                model=model,
                prompt=_build_organizer_prompt(export_dir, batch),
                idle_timeout=idle_timeout,
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
    except FileNotFoundError:
        log.error(
            f"Knowledge organizer: opencode not found at '{opencode_path}'. "
            "Install opencode or set OPENCODE_PATH."
        )
    except Exception as e:
        log.error(f"Knowledge organizer: unexpected error: {e}")

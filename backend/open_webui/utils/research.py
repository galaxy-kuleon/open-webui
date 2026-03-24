"""
/research command handler

Spawns OpenCode subprocess to research the knowledge export directory,
generates a report, and returns it for injection into chat context.

The report is also saved as a downloadable file.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import threading as _threading

log = logging.getLogger(__name__)

# Track active research subprocesses for cleanup on shutdown
_active_processes: set[subprocess.Popen] = set()
_active_processes_lock = _threading.Lock()


def kill_all_research_processes():
    """Kill all tracked research subprocesses. Called on server shutdown."""
    with _active_processes_lock:
        if not _active_processes:
            return
        import signal

        log.info(f"Shutting down: killing {len(_active_processes)} research subprocess(es)")
        for proc in list(_active_processes):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                log.info(f"Killed research process group PID {proc.pid}")
            except (ProcessLookupError, OSError):
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
        _active_processes.clear()

RESEARCH_SYSTEM_PROMPT = """You are a research assistant with access to a knowledge base directory containing markdown files.
Each .md file is a processed document, and each .index.md file is a structured index of that document.

Your task:
1. Search through the available markdown files to find information relevant to the user's research query
2. Cross-reference information across multiple files when applicable
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
    opencode_path: str = "opencode",
    model: Optional[str] = None,
    idle_timeout: int = 600,
    event_emitter=None,
) -> dict:
    """
    Run OpenCode research on the knowledge base directory.

    Args:
        query: The research question
        kb_dir: Path to the knowledge export directory
        opencode_path: Path to the opencode CLI binary
        model: Model to use (provider/model format), None for default
        idle_timeout: Kill subprocess only if idle (no output) for this many seconds
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

    # Build the opencode command
    full_prompt = (
        f"{RESEARCH_SYSTEM_PROMPT}\n\n"
        f"Research query: {query}\n\n"
        f"Search through all .md and .index.md files in this directory and subdirectories. "
        f"Generate a comprehensive research report."
    )

    cmd = [
        opencode_path,
        "run",
        "--format",
        "json",
        "--dir",
        kb_dir,
    ]
    if model:
        # Convert Open WebUI format (provider.model) to OpenCode format (provider/model)
        opencode_model = model.replace(".", "/", 1) if "." in model else model
        cmd.extend(["--model", opencode_model])
    cmd.append(full_prompt)

    # Sync provider config to KB dir before spawning
    from open_webui.utils.opencode import sync_opencode_config_to_dir
    sync_opencode_config_to_dir(kb_dir)

    # Clean env to avoid venv interference
    env = os.environ.copy()
    for key in ["VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONHOME", "PYTHONPATH"]:
        env.pop(key, None)

    log.info(f"Research: starting opencode subprocess for query: {query[:80]}, model: {model or '(default)'}")

    try:
        result = await asyncio.to_thread(_run_subprocess, cmd, env, idle_timeout)
    except Exception as e:
        log.error(f"Research subprocess failed: {e}")
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

    # Parse report from opencode output
    report = _extract_report_from_output(result.stdout)
    if not report and result.stdout:
        log.warning(
            f"Research: failed to extract text from opencode output "
            f"({len(result.stdout)} chars raw), likely unparseable JSONL"
        )

    if not report:
        error_msg = result.stderr or "OpenCode returned no output"
        log.error(f"Research: no report generated. stderr: {error_msg}")
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "research",
                        "description": "Research completed but no report generated",
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

    # Save report as downloadable file
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


def _run_subprocess(cmd: list, env: dict, idle_timeout: int) -> subprocess.CompletedProcess:
    """Run the opencode subprocess with idle-based timeout.

    Only kills the process if no stdout/stderr output for `idle_timeout` seconds.
    Called via asyncio.to_thread (blocking).
    """
    proc = subprocess.Popen(
        cmd,
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
            log.info(f"[research:{label}] {text.rstrip()[:2500] + ' ... ' + text.rstrip()[-2500:] if len(text.rstrip()) > 5000 else text.rstrip()}")
        stream.close()

    t_out = _threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks, "stdout"), daemon=True)
    t_err = _threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks, "stderr"), daemon=True)
    t_out.start()
    t_err.start()

    # Poll for idle timeout
    while proc.poll() is None:
        time.sleep(10)
        idle_secs = time.monotonic() - activity["last"]
        if idle_secs >= idle_timeout:
            import signal

            log.error(f"Research: OpenCode idle-timed out (no output for {int(idle_secs)}s)")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                proc.kill()
            proc.wait()
            with _active_processes_lock:
                _active_processes.discard(proc)
            t_out.join(timeout=5)
            t_err.join(timeout=5)
            raise subprocess.TimeoutExpired(cmd, idle_timeout)

    t_out.join(timeout=10)
    t_err.join(timeout=10)

    with _active_processes_lock:
        _active_processes.discard(proc)

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )


def _extract_report_from_output(output: str) -> Optional[str]:
    """
    Extract the assistant's text from opencode JSON output.
    opencode --format json outputs one JSON event per line.

    Text content can appear in multiple formats:
    - {"type": "text", "part": {"type": "text", "text": "..."}}  (opencode v1)
    - {"type": "text", "content": "..."}
    - {"role": "assistant", "content": "..."}
    """
    if not output:
        return None

    text_chunks = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue

            event_type = event.get("type", "")

            # opencode v1 format: nested in event.part
            if event_type == "text" and "part" in event:
                part = event["part"]
                if isinstance(part, dict):
                    text = part.get("text", "") or part.get("content", "")
                    if text:
                        text_chunks.append(text)
                        continue

            # Flat format: type=text with content
            if event_type in ("text", "content"):
                text = event.get("content", "") or event.get("text", "")
                if text:
                    text_chunks.append(text)
                    continue

            # Role-based format
            if event.get("role") == "assistant" and event.get("content"):
                text_chunks.append(event["content"])
                continue

            # Nested message format
            if "message" in event and isinstance(event["message"], dict):
                msg = event["message"]
                if msg.get("role") == "assistant" and msg.get("content"):
                    text_chunks.append(msg["content"])

        except json.JSONDecodeError:
            pass

    if text_chunks:
        return "".join(text_chunks)

    return None


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

    frontmatter = (
        f"---\n"
        f"type: research_report\n"
        f"query: {query}\n"
        f"generated_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"---\n\n"
    )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(frontmatter + report)

    return report_path

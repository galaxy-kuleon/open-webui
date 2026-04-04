"""
Utilities for running opencode agent skills.

Supports two execution modes:
  1. Sandbox mode: skill files are copied into a temp dir, opencode runs there,
     output files are collected by scanning all modified files, then cleaned up.
  2. Direct-dir (work_dir) mode: opencode runs directly in the user's project
     directory. Output files are collected ONLY from a well-known `output/`
     subdirectory (never scanning the full project tree). No cleanup is performed.

Handles config generation, execution with streaming, output file collection,
and cleanup (sandbox mode only).
"""

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from open_webui.env import OPENCODE_PATH, OPENCODE_MODEL_NAME_MIDDLE_NAME

log = logging.getLogger(__name__)

# Concurrency limit per user
_user_semaphores: dict[str, asyncio.Semaphore] = {}
MAX_CONCURRENT_PER_USER = 2
DEFAULT_IDLE_TIMEOUT = (
    3600  # seconds — kill only if idle (no output) for this long (60 min for VLM tasks)
)
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024  # 50MB

# Global registry of active OpenCode subprocesses (for cleanup on shutdown)
_active_processes: set[asyncio.subprocess.Process] = set()


def kill_all_opencode_processes():
    """Kill all tracked OpenCode subprocesses. Called on server shutdown."""
    import signal

    if not _active_processes:
        return
    log.info(f"Shutting down: killing {len(_active_processes)} OpenCode subprocess(es)")
    for proc in list(_active_processes):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            log.info(f"Killed OpenCode process group PID {proc.pid}")
        except (ProcessLookupError, OSError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    _active_processes.clear()


def _get_user_semaphore(user_id: str) -> asyncio.Semaphore:
    if user_id not in _user_semaphores:
        _user_semaphores[user_id] = asyncio.Semaphore(MAX_CONCURRENT_PER_USER)
    return _user_semaphores[user_id]


def generate_opencode_config(
    openai_api_base_urls: list[str],
    openai_api_keys: list[str],
    ollama_base_urls: list[str],
    openai_api_configs: dict | None = None,
    model_override: str | None = None,
) -> dict:
    """
    Update ~/.config/opencode/opencode.json with provider info from OpenWebUI.

    Reads the existing config and updates provider connection details
    (baseURL, apiKey). Existing model settings are preserved unless the
    top-level model is missing or stale relative to the configured agent model.

    If model_override is given (e.g. "lmstudio/qwen3.5-122b-a10b"), it replaces
    model, and all agent.*.model entries.

    Returns the merged config dict.
    """
    config_dir = Path.home() / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"

    # Read existing config — this is the user's curated template
    existing_config = {}
    if config_path.exists():
        try:
            existing_config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing_providers = existing_config.get("provider", {})

    # Update provider connection details from OpenWebUI, preserving everything else
    api_configs = openai_api_configs or {}
    for i, (url, key) in enumerate(zip(openai_api_base_urls, openai_api_keys)):
        if not url:
            continue

        cfg = api_configs.get(str(i), {})
        prefix_id = cfg.get("prefix_id", "")
        provider_id = prefix_id if prefix_id else (f"openai_{i}" if i > 0 else "openai")
        base_url = url.rstrip("/")

        if provider_id in existing_providers:
            # Preserve existing entry, only update connection details
            existing = existing_providers[provider_id]
            if "options" in existing:
                existing["options"]["baseURL"] = base_url
                if key:
                    existing["options"]["apiKey"] = key
            else:
                existing["baseURL"] = base_url
                if key:
                    existing["apiKey"] = key
        # If provider not in existing config, skip — don't pollute user's config
        # with providers that may not have the correct opencode format.

    # Ollama: only update existing entries, don't add new ones
    for i, url in enumerate(ollama_base_urls):
        if not url:
            continue
        provider_id = f"ollama_{i}" if i > 0 else "ollama"
        base = url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        if provider_id in existing_providers:
            existing = existing_providers[provider_id]
            if "options" in existing:
                existing["options"]["baseURL"] = base
            else:
                existing["baseURL"] = base

    # Merge: start from existing config, update provider
    config = existing_config.copy()
    config["provider"] = existing_providers
    config.setdefault("$schema", "https://opencode.ai/config.json")
    config.setdefault("permission", {"*": "allow"})

    # Optional: override model across all agent configs
    if model_override:
        config["model"] = model_override
        if "agent" in config:
            for agent_cfg in config["agent"].values():
                if isinstance(agent_cfg, dict) and "model" in agent_cfg:
                    agent_cfg["model"] = model_override
    else:
        current_model = config.get("model") or ""
        current_provider_id, _, current_model_name = current_model.partition("/")
        current_provider_cfg = config.get("provider", {}).get(current_provider_id, {})
        current_provider_models = (
            current_provider_cfg.get("models", {})
            if isinstance(current_provider_cfg, dict)
            else {}
        )
        current_model_known = bool(
            current_model_name
            and (
                not current_provider_models
                or current_model_name in current_provider_models
            )
        )

        agent_models = {
            agent_cfg.get("model")
            for agent_cfg in config.get("agent", {}).values()
            if isinstance(agent_cfg, dict) and agent_cfg.get("model")
        }
        if (not current_model or not current_model_known) and len(agent_models) == 1:
            candidate_model = next(iter(agent_models))
            candidate_provider_id, _, candidate_model_name = candidate_model.partition(
                "/"
            )
            candidate_provider_cfg = config.get("provider", {}).get(
                candidate_provider_id, {}
            )
            candidate_provider_models = (
                candidate_provider_cfg.get("models", {})
                if isinstance(candidate_provider_cfg, dict)
                else {}
            )
            if candidate_model_name and (
                not candidate_provider_models
                or candidate_model_name in candidate_provider_models
            ):
                config["model"] = candidate_model

    config_path.write_text(json.dumps(config, indent=2))

    log.debug(f"Wrote opencode config to {config_path}")
    return config


def sync_opencode_config_to_dir(target_dir: str) -> None:
    """
    Sync OpenCode config to a project directory's opencode.json.

    Reads the global config (~/.config/opencode/opencode.json) which has
    the user's provider/model settings, merges with permission: allow,
    and writes to {target_dir}/opencode.json.
    """
    global_config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    target_path = Path(target_dir) / "opencode.json"

    # Start with existing project config or empty
    project_config = {}
    if target_path.exists():
        try:
            project_config = json.loads(target_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Read global config for provider settings
    if global_config_path.exists():
        try:
            global_config = json.loads(global_config_path.read_text())
            for key in ("provider", "model", "small_model", "agent"):
                if key in global_config:
                    project_config[key] = global_config[key]
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to read global opencode config: {e}")

    # Ensure permission is set
    project_config.setdefault("$schema", "https://opencode.ai/config.json")
    project_config["permission"] = "allow"

    try:
        target_path.write_text(json.dumps(project_config, indent=2))
        log.debug(f"Synced opencode config to {target_path}")
    except OSError as e:
        log.warning(f"Failed to write opencode config to {target_path}: {e}")


def setup_sandbox(skill_id: str, skill_disk_path: str, skill_name: str = "") -> str:
    """
    Create a sandbox directory for opencode execution.

    1. Create /tmp/opencode-sandbox-{uuid}/
    2. Copy skill files into {sandbox}/.claude/skills/{skill_id}/
    3. Generate AGENTS.md at sandbox root
    4. Return sandbox path
    """
    sandbox_id = str(uuid.uuid4())
    sandbox_dir = f"/tmp/opencode-sandbox-{sandbox_id}"
    os.makedirs(sandbox_dir, exist_ok=True)

    # Create skills directory and copy skill files
    skills_dest = os.path.join(sandbox_dir, ".claude", "skills", skill_id)
    os.makedirs(skills_dest, exist_ok=True)

    if os.path.isdir(skill_disk_path):
        shutil.copytree(skill_disk_path, skills_dest, dirs_exist_ok=True)
    else:
        log.warning(f"Skill disk path does not exist: {skill_disk_path}")

    # Generate AGENTS.md
    agents_md = (
        f"# Agent Instructions\n\n"
        f"You have access to the **{skill_name or skill_id}** skill.\n"
        f"The skill files are located in `.claude/skills/{skill_id}/`.\n"
        f"Read the SKILL.md file for detailed instructions and follow them.\n\n"
        f"## Critical Rules\n\n"
        f"- **File paths MUST NEVER contain spaces.** Replace spaces with hyphens or underscores.\n"
        f"- When handling CJK (Chinese/Japanese/Korean) filenames, ALWAYS quote paths "
        f'in shell commands with double quotes: `"path/to/中文檔名.pdf"`\n'
        f"- Never insert spaces into CJK filenames or directory names.\n"
        f"- When creating new files or directories, use only ASCII lowercase, "
        f"numbers, hyphens, and underscores in path components.\n"
    )
    agents_path = os.path.join(sandbox_dir, "AGENTS.md")
    with open(agents_path, "w") as f:
        f.write(agents_md)

    log.info(f"Created sandbox at {sandbox_dir} for skill {skill_id}")
    return sandbox_dir


def _convert_model_name(model: str) -> str:
    """
    Convert model name for opencode based on OPENCODE_MODEL_NAME_MIDDLE_NAME env var.

    If OPENCODE_MODEL_NAME_MIDDLE_NAME is set (e.g., "qwen"), converts:
        "provider.modelname" → "provider/{middle_name}/modelname"

    Example:
        "lmstudio.qwen3.5-122b-a10b" → "lmstudio/qwen/qwen3.5-122b-a10b"
    """
    if not model or not isinstance(model, str):
        return model

    middle_name = OPENCODE_MODEL_NAME_MIDDLE_NAME
    if not middle_name:
        return model

    # Check if model has provider prefix format (contains ".")
    if "." in model:
        provider, _, model_name = model.partition(".")
        if provider and model_name:
            return f"{provider}/{middle_name}/{model_name}"

    return model


async def run_opencode(
    sandbox_dir: str,
    message: str,
    model: str,
    skill_name: str,
    event_emitter: Optional[Callable] = None,
    idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
) -> str:
    """
    Execute opencode CLI in the sandbox and stream events to the chat.

    Returns the collected text output as a string.
    """
    prompt = f"Use {skill_name} skill to ultra-reify end-user's request: {message}"

    cmd = [
        OPENCODE_PATH,
        "run",
        "--format",
        "json",
        "--thinking",
        "--dir",
        sandbox_dir,
    ]
    if model and isinstance(model, str):
        converted_model = _convert_model_name(model)
        cmd.extend(["--model", converted_model])
    cmd.append(prompt)

    log.info(f"Running opencode: {' '.join(str(c) for c in cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
        stderr=asyncio.subprocess.PIPE,
    )
    _active_processes.add(proc)

    collected_text = []
    stderr_output = []
    # Mutable container so both stdout reader and stderr reader can update it.
    # Any output on either stream counts as activity for idle-timeout purposes.
    last_activity_ns = [time.monotonic()]

    async def _read_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            last_activity_ns[0] = time.monotonic()
            text = line.decode("utf-8", errors="replace")
            stderr_output.append(text)
            log.info(
                f"[opencode:stderr] {text.rstrip()[:2500] + ' ... ' + text.rstrip()[-2500:] if len(text.rstrip()) > 5000 else text.rstrip()}"
            )

    stderr_task = asyncio.create_task(_read_stderr())

    try:

        async def _read_line_with_idle_check():
            """Read a line, raising TimeoutError if idle too long."""
            while True:
                remaining = idle_timeout - (time.monotonic() - last_activity_ns[0])
                if remaining <= 0:
                    raise TimeoutError(f"idle for {idle_timeout}s")
                try:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=min(remaining, 30)
                    )
                    if line:
                        last_activity_ns[0] = time.monotonic()
                    return line
                except asyncio.TimeoutError:
                    # No data yet — check if still within idle window
                    # (stderr activity may have extended the window)
                    if time.monotonic() - last_activity_ns[0] >= idle_timeout:
                        raise TimeoutError(f"idle for {idle_timeout}s")
                    # Otherwise loop and keep waiting

        while True:
            line = await _read_line_with_idle_check()
            if not line:
                break

            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            log.info(
                f"[opencode:stdout] {line_str[:2500] + ' ... ' + line_str[-2500:] if len(line_str) > 5000 else line_str}"
            )

            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            # opencode JSON streaming nests data in event["part"]
            part = event.get("part", {})

            if event_type == "text":
                text = part.get("text", "") or event.get(
                    "content", event.get("text", "")
                )
                if text and text.strip():
                    collected_text.append(text)
                    if event_emitter:
                        await event_emitter(
                            {
                                "type": "status",
                                "data": {
                                    "action": "agent_skill",
                                    "sub_action": "output",
                                    "description": text.strip()[:500],
                                    "done": False,
                                },
                            }
                        )

            elif event_type == "reasoning":
                thinking = part.get("text", "") or event.get("content", "")
                if thinking and thinking.strip() and event_emitter:
                    await event_emitter(
                        {
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "thinking",
                                "description": thinking.strip()[:500],
                                "done": False,
                            },
                        }
                    )

            elif event_type == "tool_use":
                # opencode nests tool info in part.state
                state = part.get("state", {})
                tool_name = part.get("tool", event.get("name", "tool"))
                tool_title = state.get("title", "")
                tool_input_data = state.get("input", {})
                tool_desc = tool_title or tool_input_data.get("description", "")
                if isinstance(tool_input_data, dict):
                    cmd = tool_input_data.get("command", "")
                    if cmd:
                        tool_desc = (
                            f"{tool_desc}: {cmd[:200]}" if tool_desc else cmd[:300]
                        )

                if event_emitter:
                    await event_emitter(
                        {
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "tool_use",
                                "description": f"{tool_name}: {tool_desc}"[:500]
                                if tool_desc
                                else tool_name,
                                "done": False,
                            },
                        }
                    )

                # Collect tool output if completed
                tool_output = state.get("output", "")
                if isinstance(tool_output, str) and tool_output.strip():
                    # Only collect short outputs to avoid noise
                    if len(tool_output) < 2000:
                        collected_text.append(tool_output)

            elif event_type == "error":
                error_msg = event.get("error", event.get("message", "Unknown error"))
                if event_emitter:
                    await event_emitter(
                        {
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "error",
                                "description": str(error_msg)[:500],
                                "done": False,
                            },
                        }
                    )

            elif event_type == "result":
                result_text = event.get("result", event.get("content", ""))
                if not result_text:
                    result_text = part.get("result", part.get("text", ""))
                if result_text:
                    if isinstance(result_text, str):
                        collected_text.append(result_text)
                    else:
                        collected_text.append(
                            json.dumps(result_text, ensure_ascii=False)
                        )

        await proc.wait()

    except TimeoutError:
        elapsed = int(time.monotonic() - last_activity_ns[0])
        import signal

        log.warning(
            f"opencode idle-timed out (no output for {elapsed}s), killing process group"
        )
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        await proc.wait()
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "agent_skill",
                        "description": f"Agent skill idle-timed out (no activity for {idle_timeout}s)",
                        "done": True,
                    },
                }
            )
        return f"Error: opencode idle-timed out (no output for {idle_timeout} seconds)."

    finally:
        _active_processes.discard(proc)
        await stderr_task

    if proc.returncode != 0 and not collected_text:
        stderr_str = "".join(stderr_output)
        log.error(f"opencode exited with code {proc.returncode}: {stderr_str}")
        return f"Error: opencode exited with code {proc.returncode}. {stderr_str[:500]}"

    return "\n".join(str(t) for t in collected_text) or "(No output)"


def collect_output_files(sandbox_dir: str, start_time: float) -> list[Path]:
    """
    Collect output files from a SANDBOX directory (mode 1).

    Walks the entire sandbox tree and returns files modified after start_time.
    Safe because the sandbox is a clean temp dir we created.
    Skips .claude/, AGENTS.md, and hidden config files.

    For work_dir mode, use collect_output_files_from_work_dir() instead.
    """
    output_files = []
    sandbox = Path(sandbox_dir)

    for path in sandbox.rglob("*"):
        if not path.is_file():
            continue

        # Skip internal files
        rel = path.relative_to(sandbox)
        parts = rel.parts
        if any(p.startswith(".") for p in parts):
            continue
        if parts[0] == "AGENTS.md":
            continue

        # Check modification time
        try:
            if path.stat().st_mtime >= start_time:
                output_files.append(path)
        except OSError:
            continue

    return output_files


def collect_output_files_from_work_dir(work_dir: str) -> list[Path]:
    """
    Collect output files from a WORK_DIR (direct-dir mode, mode 2).

    Unlike sandbox collection, this does NOT scan the entire project tree.
    Only files inside {work_dir}/output/ are collected. This is safe because:
      - work_dir is the user's real project directory (.venv, .git, etc.)
      - Scanning the full tree would pick up irrelevant/dangerous files
      - Skills running in work_dir mode are expected to place their outputs
        in the output/ subdirectory

    No start_time filtering is applied — ALL files in output/ are returned.
    The output/ directory is the skill's contract for where results go.

    Does NOT clean up the work_dir (it's the user's project, not a temp dir).
    """
    output_dir = Path(work_dir) / "output"

    if not output_dir.is_dir():
        log.debug(f"No output/ directory in work_dir {work_dir}")
        return []

    # Resolve output_dir to its real path once, so all containment checks
    # compare against the canonical location (no symlink components).
    resolved_output_dir = output_dir.resolve()

    output_files = []
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue

        # SECURITY: resolve() follows all symlinks to the real filesystem path.
        # We then verify the resolved path is still under output_dir.
        # This prevents symlink escape (e.g. output/evil -> /etc/passwd).
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_output_dir)
        except ValueError:
            log.warning(
                f"Skipping symlink escape: {path} resolves to {resolved_path} "
                f"which is outside {resolved_output_dir}"
            )
            continue

        # Skip hidden files even within output/
        rel = path.relative_to(output_dir)
        if any(p.startswith(".") for p in rel.parts):
            continue
        output_files.append(path)

    log.info(f"Collected {len(output_files)} output file(s) from {output_dir}")
    return output_files


def cleanup_sandbox(sandbox_dir: str):
    """Remove sandbox directory."""
    try:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        log.debug(f"Cleaned up sandbox: {sandbox_dir}")
    except Exception as e:
        log.warning(f"Failed to cleanup sandbox {sandbox_dir}: {e}")

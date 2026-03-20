"""
Utilities for running opencode agent skills in sandboxed environments.

Handles sandbox creation, opencode config generation, execution with streaming,
output file collection, and cleanup.
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

from open_webui.env import OPENCODE_PATH

log = logging.getLogger(__name__)

# Concurrency limit per user
_user_semaphores: dict[str, asyncio.Semaphore] = {}
MAX_CONCURRENT_PER_USER = 2
DEFAULT_TIMEOUT = 300  # seconds
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024  # 50MB


def _get_user_semaphore(user_id: str) -> asyncio.Semaphore:
    if user_id not in _user_semaphores:
        _user_semaphores[user_id] = asyncio.Semaphore(MAX_CONCURRENT_PER_USER)
    return _user_semaphores[user_id]


def generate_opencode_config(
    openai_api_base_urls: list[str],
    openai_api_keys: list[str],
    ollama_base_urls: list[str],
) -> dict:
    """
    Generate opencode.json config from OpenWebUI's connected providers.
    Write to ~/.config/opencode/opencode.json.
    Returns the config dict.
    """
    providers = {}

    # Map OpenAI-compatible providers
    for i, (url, key) in enumerate(zip(openai_api_base_urls, openai_api_keys)):
        if not url:
            continue
        provider_id = f"openai_{i}" if i > 0 else "openai"
        providers[provider_id] = {
            "baseURL": url.rstrip("/"),
            "apiKey": key or "",
        }

    # Map Ollama providers (use /v1 endpoint for OpenAI compat)
    for i, url in enumerate(ollama_base_urls):
        if not url:
            continue
        provider_id = f"ollama_{i}" if i > 0 else "ollama"
        base = url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        providers[provider_id] = {
            "baseURL": base,
            "apiKey": "ollama",
        }

    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"*": "allow"},
        "provider": providers,
    }

    # Write to global config location
    config_dir = Path.home() / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))

    log.debug(f"Wrote opencode config to {config_path}")
    return config


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
        f"Read the SKILL.md file for detailed instructions and follow them.\n"
    )
    agents_path = os.path.join(sandbox_dir, "AGENTS.md")
    with open(agents_path, "w") as f:
        f.write(agents_md)

    log.info(f"Created sandbox at {sandbox_dir} for skill {skill_id}")
    return sandbox_dir


async def run_opencode(
    sandbox_dir: str,
    message: str,
    model: str,
    skill_name: str,
    event_emitter: Optional[Callable] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """
    Execute opencode CLI in the sandbox and stream events to the chat.

    Returns the collected text output as a string.
    """
    prompt = f"Use {skill_name} skill to ultra-reify end-user's request: {message}"

    cmd = [
        OPENCODE_PATH,
        "run",
        "--format", "json",
        "--thinking",
        "--dir", sandbox_dir,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)

    log.info(f"Running opencode: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    collected_text = []
    stderr_output = []

    async def _read_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_output.append(line.decode("utf-8", errors="replace"))

    stderr_task = asyncio.create_task(_read_stderr())

    try:
        async with asyncio.timeout(timeout):
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    event = json.loads(line_str)
                except json.JSONDecodeError:
                    log.debug(f"Non-JSON line from opencode: {line_str}")
                    continue

                event_type = event.get("type", "")

                if event_type == "text" or event_type == "content":
                    text = event.get("content", event.get("text", ""))
                    if text:
                        collected_text.append(text)
                    if event_emitter:
                        await event_emitter({
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "output",
                                "description": text[:500] if text else "",
                                "done": False,
                            },
                        })

                elif event_type == "thinking":
                    thinking_text = event.get("content", event.get("thinking", ""))
                    if event_emitter and thinking_text:
                        await event_emitter({
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "thinking",
                                "description": thinking_text[:500],
                                "done": False,
                            },
                        })

                elif event_type == "tool_use":
                    tool_name = event.get("name", event.get("tool", ""))
                    tool_input = event.get("input", event.get("args", ""))
                    if isinstance(tool_input, dict):
                        tool_input = json.dumps(tool_input, ensure_ascii=False)[:500]
                    elif isinstance(tool_input, str):
                        tool_input = tool_input[:500]
                    else:
                        tool_input = str(tool_input)[:500]
                    if event_emitter:
                        await event_emitter({
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "tool_use",
                                "description": f"{tool_name}: {tool_input}",
                                "tool_name": tool_name,
                                "tool_input": tool_input,
                                "done": False,
                            },
                        })

                elif event_type == "tool_result":
                    result_text = event.get("content", event.get("output", ""))
                    if isinstance(result_text, str) and result_text:
                        collected_text.append(result_text)

                elif event_type == "error":
                    error_msg = event.get("error", event.get("message", "Unknown error"))
                    if event_emitter:
                        await event_emitter({
                            "type": "status",
                            "data": {
                                "action": "agent_skill",
                                "sub_action": "error",
                                "description": str(error_msg)[:500],
                                "done": False,
                            },
                        })

                elif event_type == "result":
                    # Final result event
                    result_text = event.get("result", event.get("content", ""))
                    if result_text:
                        collected_text.append(result_text)

            await proc.wait()

    except TimeoutError:
        log.warning(f"opencode timed out after {timeout}s, killing process")
        proc.kill()
        await proc.wait()
        if event_emitter:
            await event_emitter({
                "type": "status",
                "data": {
                    "action": "agent_skill",
                    "description": f"Agent skill timed out after {timeout}s",
                    "done": True,
                },
            })
        return f"Error: opencode execution timed out after {timeout} seconds."

    finally:
        await stderr_task

    if proc.returncode != 0 and not collected_text:
        stderr_str = "".join(stderr_output)
        log.error(f"opencode exited with code {proc.returncode}: {stderr_str}")
        return f"Error: opencode exited with code {proc.returncode}. {stderr_str[:500]}"

    return "\n".join(collected_text) or "(No output)"


def collect_output_files(sandbox_dir: str, start_time: float) -> list[Path]:
    """
    Walk the sandbox and find files modified after start_time.
    Skip .claude/, AGENTS.md, and hidden config files.
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


def cleanup_sandbox(sandbox_dir: str):
    """Remove sandbox directory."""
    try:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        log.debug(f"Cleaned up sandbox: {sandbox_dir}")
    except Exception as e:
        log.warning(f"Failed to cleanup sandbox {sandbox_dir}: {e}")

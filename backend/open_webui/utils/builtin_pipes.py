"""Auto-register codebase-built-in pipe functions at startup.

Pipe functions must exist in the `function` DB table to be discovered by
get_function_models().  This module bridges the gap for pipes that ship
as .py files in the repo rather than being uploaded via the admin UI.
"""

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Each entry: id, display name, path relative to this file's parent package.
_BUILTIN_PIPES = [
    {
        "id": "hermes_agent",
        "name": "Hermes Agent",
        "description": "Proxies chat to hermes-agent API server with SSE streaming and tool progress",
        "path": Path(__file__).resolve().parent.parent / "pipes" / "hermes_agent.py",
    },
]


def ensure_builtin_pipes() -> None:
    """Upsert built-in pipe functions into the DB.

    - If the function doesn't exist, insert it (type=pipe, is_active=True).
    - If it exists but the on-disk source changed (content hash mismatch),
      update the content.
    - If it exists and the hash matches, do nothing (no DB write on restart).
    """
    try:
        from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
        from open_webui.models.users import Users
    except Exception as e:
        log.warning(f"Cannot import models for builtin pipe registration: {e}")
        return

    # We need an owner user_id.  Prefer super-admin, fall back to first user.
    admin = Users.get_super_admin_user()
    if admin is None:
        admin = Users.get_first_user()
    if admin is None:
        log.info("No users exist yet — skipping builtin pipe registration")
        return

    for pipe_def in _BUILTIN_PIPES:
        pipe_path: Path = pipe_def["path"]
        if not pipe_path.is_file():
            log.warning(f"Builtin pipe source not found: {pipe_path}")
            continue

        content = pipe_path.read_text(encoding="utf-8")
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        existing = Functions.get_function_by_id(pipe_def["id"])
        if existing:
            existing_hash = ""
            if existing.meta:
                meta_dict = existing.meta if isinstance(existing.meta, dict) else existing.meta.model_dump()
                existing_hash = meta_dict.get("manifest", {}).get("content_hash", "")
            if existing_hash == content_hash:
                log.debug(f"Builtin pipe '{pipe_def['id']}' is up to date")
                continue

            # Content changed — update
            try:
                Functions.update_function_by_id(
                    pipe_def["id"],
                    {
                        "content": content,
                        "meta": {
                            "description": pipe_def["description"],
                            "manifest": {"content_hash": content_hash, "builtin": True},
                        },
                    },
                )
                log.info(f"Updated builtin pipe: {pipe_def['id']}")
            except Exception as e:
                log.error(f"Failed to update builtin pipe '{pipe_def['id']}': {e}")
        else:
            # New — insert
            try:
                Functions.insert_new_function(
                    admin.id,
                    "pipe",
                    FunctionForm(
                        id=pipe_def["id"],
                        name=pipe_def["name"],
                        content=content,
                        meta=FunctionMeta(
                            description=pipe_def["description"],
                            manifest={"content_hash": content_hash, "builtin": True},
                        ),
                    ),
                )
                # Activate the pipe
                Functions.update_function_by_id(
                    pipe_def["id"], {"is_active": True}
                )
                log.info(f"Registered builtin pipe: {pipe_def['id']}")
            except Exception as e:
                log.error(f"Failed to register builtin pipe '{pipe_def['id']}': {e}")

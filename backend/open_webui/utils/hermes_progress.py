"""Translate Hermes tool lifecycle SSE payloads into OpenWebUI statuses."""

from __future__ import annotations

import time
from typing import Any, MutableMapping


_MAX_TOOL_LABEL_CHARS = 120


def hermes_tool_status_event(
    payload: Any,
    started_at_by_call_id: MutableMapping[str, float],
    *,
    now: float | None = None,
) -> dict | None:
    """Return one existing OpenWebUI ``status`` event for Hermes lifecycle data.

    Hermes emits named ``hermes.tool.progress`` SSE events. OpenWebUI's generic
    provider reader sees the following ``data:`` line but intentionally ignores
    unknown payloads with no OpenAI ``choices``. Recognize only the strict
    correlated lifecycle shape so arbitrary provider JSON cannot become UI.
    """
    if not isinstance(payload, dict):
        return None
    state = payload.get("status")
    if state not in {"running", "completed"}:
        return None
    call_id = str(payload.get("toolCallId") or "").strip()
    tool = str(payload.get("tool") or "").strip()
    if not call_id or not tool:
        return None

    observed_at = float(time.time() if now is None else now)
    tool = tool[:_MAX_TOOL_LABEL_CHARS]
    if state == "running":
        started_at = started_at_by_call_id.setdefault(call_id, observed_at)
        description = f"Running {tool}"
        done = False
        elapsed_seconds = None
    else:
        started_at = started_at_by_call_id.pop(call_id, observed_at)
        elapsed_seconds = max(0.0, observed_at - started_at)
        description = f"Completed {tool}"
        done = True

    data = {
        "action": "hermes_tool",
        "description": description,
        "done": done,
        "tool": tool,
        "tool_call_id": call_id,
        "started_at": started_at,
    }
    if elapsed_seconds is not None:
        data["elapsed_seconds"] = round(elapsed_seconds, 1)
    return {"type": "status", "data": data}

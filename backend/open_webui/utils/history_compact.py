"""Compact tool-call payloads in chat history before they are re-sent to an LLM.

Why this exists
---------------
Batch audits (Path B / Hermes) put full file text into tool results. OWUI stores
those results inside assistant history as ``<details type="tool_calls">`` HTML
(and/or structured ``output`` items). On the next turn, middleware rebuilds the
LLM payload from history and re-sends every prior tool body. Once the JSON body
crosses Hermes ``MAX_REQUEST_BYTES`` (10 MB), every subsequent turn is 413 and
the chat is permanently bricked.

Raising the limit only moves the cliff. This module **cuts the re-sent input**:
tool detail blocks are replaced with a short, model-facing omission note while
final answer text is preserved. Source files remain reachable on Path B via
handoff manifest reinjection on follow-up turns.

This module is intentionally pure stdlib so unit tests and the user-flow
baseline can import it without booting the full OpenWebUI app.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any

# ── named policy (no magic numbers) ──────────────────────────────────────────

# Hermes gateway MAX_REQUEST_BYTES is 10_000_000. Leave headroom for system
# prompt, tools schema, and request envelope so we fail closed *before* the
# upstream 413 that bricks the chat with an opaque error.
DEFAULT_MAX_LLM_MESSAGES_BYTES = int(
    os.environ.get("OWUI_MAX_LLM_MESSAGES_BYTES", str(8_000_000))
)

# Structured tool-role results larger than this are truncated for history.
DEFAULT_TOOL_RESULT_MAX_CHARS = int(
    os.environ.get("OWUI_HISTORY_TOOL_RESULT_MAX_CHARS", "800")
)

TOOL_CALLS_DETAILS_RE = re.compile(
    r"<details\s+type=[\"']tool_calls[\"'][^>]*>.*?</details>\s*",
    re.DOTALL | re.IGNORECASE,
)

# Stable tokens for tests / mutation checks (must appear in compact output).
HISTORY_TOOL_OMISSION_TOKEN = "tool_call detail block"
HISTORY_TOOL_OMISSION_HINT = "Re-read attached files if you need source text again"
HISTORY_OVERSIZE_USER_MESSAGE = (
    "這次對話的歷史（含先前工具讀檔全文）在壓縮後仍超過系統上限，"
    "無法繼續在此 chat 送出。請**開新 chat** 並重新附上需要的檔案；"
    "不要在此 chat 重試（會一直失敗）。"
    " This chat history still exceeds the size limit after compacting prior "
    "tool outputs. **Open a new chat** and re-attach the files you need — "
    "retrying here will keep failing."
)


class HistoryPayloadTooLarge(Exception):
    """Raised when compacted history still exceeds the safe send budget."""

    def __init__(self, *, bytes_len: int, limit: int):
        self.bytes_len = bytes_len
        self.limit = limit
        super().__init__(HISTORY_OVERSIZE_USER_MESSAGE)


def _omission_note(count: int) -> str:
    return (
        f"[history: {count} {HISTORY_TOOL_OMISSION_TOKEN}(s) omitted from "
        f"re-sent context to keep this chat under the size limit. Final "
        f"answers above remain. {HISTORY_TOOL_OMISSION_HINT}.]"
    )


def compact_tool_details_in_text(text: str) -> tuple[str, int]:
    """Remove ``tool_calls`` HTML detail blocks from assistant display text.

    Returns ``(compacted_text, removed_block_count)``. Non-tool text (the
    final answer / audit report) is preserved.
    """
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", 0
    if "tool_calls" not in text:
        return text, 0
    matches = list(TOOL_CALLS_DETAILS_RE.finditer(text))
    if not matches:
        return text, 0
    compacted = TOOL_CALLS_DETAILS_RE.sub("", text).rstrip()
    note = _omission_note(len(matches))
    if compacted:
        return f"{compacted}\n\n{note}\n", len(matches)
    return f"{note}\n", len(matches)


def _truncate_tool_result_text(text: str, *, max_chars: int) -> str:
    if not isinstance(text, str) or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return (
        f"{text[:max_chars]}\n"
        f"...[tool result truncated for history; {omitted} chars omitted; "
        f"{HISTORY_TOOL_OMISSION_HINT}.]\n"
    )


def _compact_output_items(
    output: list[Any],
    *,
    max_tool_chars: int,
) -> tuple[list[Any], int]:
    """Compact tool bodies inside OR-aligned ``output`` items. Returns new list + removals."""
    if not isinstance(output, list):
        return output, 0
    removed = 0
    new_items: list[Any] = []
    for item in output:
        if not isinstance(item, dict):
            new_items.append(item)
            continue
        item = dict(item)
        item_type = item.get("type")

        if item_type == "message":
            content = item.get("content")
            if isinstance(content, list):
                new_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        new_parts.append(part)
                        continue
                    part = dict(part)
                    text = part.get("text")
                    if isinstance(text, str) and "tool_calls" in text:
                        part["text"], n = compact_tool_details_in_text(text)
                        removed += n
                    new_parts.append(part)
                item["content"] = new_parts
        elif item_type == "function_call_output":
            outs = item.get("output")
            if isinstance(outs, list):
                new_outs = []
                for part in outs:
                    if not isinstance(part, dict):
                        new_outs.append(part)
                        continue
                    part = dict(part)
                    text = part.get("text")
                    if isinstance(text, str) and len(text) > max_tool_chars:
                        part["text"] = _truncate_tool_result_text(
                            text, max_chars=max_tool_chars
                        )
                        removed += 1
                    new_outs.append(part)
                item["output"] = new_outs
            elif isinstance(outs, str) and len(outs) > max_tool_chars:
                item["output"] = _truncate_tool_result_text(
                    outs, max_chars=max_tool_chars
                )
                removed += 1

        new_items.append(item)
    return new_items, removed


def compact_messages_for_llm_history(
    messages: list[dict],
    *,
    max_tool_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
) -> list[dict]:
    """Return a deep-copied message list safe to re-send as LLM history.

    * Strips ``<details type="tool_calls">`` blocks from string content.
    * Compacts structured ``output`` tool bodies / embedded details.
    * Truncates ``role=tool`` / ``role=function`` content.
    Does **not** mutate the caller's list (DB-facing copies stay intact when
    callers pass a copy; process_messages builds a new list either way).
    """
    if not isinstance(messages, list):
        return messages

    compacted: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            compacted.append(message)
            continue
        message = deepcopy(message)
        role = message.get("role")

        content = message.get("content")
        if isinstance(content, str) and "tool_calls" in content:
            message["content"], _ = compact_tool_details_in_text(content)
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    part = dict(part)
                    text = part.get("text")
                    if isinstance(text, str) and "tool_calls" in text:
                        part["text"], _ = compact_tool_details_in_text(text)
                    new_parts.append(part)
                else:
                    new_parts.append(part)
            message["content"] = new_parts

        if isinstance(message.get("output"), list):
            message["output"], _ = _compact_output_items(
                message["output"], max_tool_chars=max_tool_chars
            )

        if role in {"tool", "function"} and isinstance(message.get("content"), str):
            message["content"] = _truncate_tool_result_text(
                message["content"], max_chars=max_tool_chars
            )

        compacted.append(message)
    return compacted


def estimate_messages_payload_bytes(messages: list[dict]) -> int:
    """JSON UTF-8 byte size of a messages list (proxy for request body bulk)."""
    try:
        return len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def ensure_messages_within_budget(
    messages: list[dict],
    *,
    limit_bytes: int = DEFAULT_MAX_LLM_MESSAGES_BYTES,
) -> list[dict]:
    """Compact then enforce the send budget; raise HistoryPayloadTooLarge if still over."""
    compacted = compact_messages_for_llm_history(messages)
    size = estimate_messages_payload_bytes(compacted)
    if size > limit_bytes:
        raise HistoryPayloadTooLarge(bytes_len=size, limit=limit_bytes)
    return compacted


def project_history_payload_stats(
    messages: list[dict],
    *,
    limit_bytes: int = DEFAULT_MAX_LLM_MESSAGES_BYTES,
) -> dict[str, Any]:
    """Privacy-safe before/after stats for one message chain (baseline / RBV)."""
    before = estimate_messages_payload_bytes(messages)
    compacted = compact_messages_for_llm_history(messages)
    after = estimate_messages_payload_bytes(compacted)
    return {
        "bytes_before": before,
        "bytes_after": after,
        "bytes_saved": max(0, before - after),
        "over_limit_before": before > limit_bytes,
        "over_limit_after": after > limit_bytes,
        "limit_bytes": limit_bytes,
    }


__all__ = [
    "DEFAULT_MAX_LLM_MESSAGES_BYTES",
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "HISTORY_OVERSIZE_USER_MESSAGE",
    "HISTORY_TOOL_OMISSION_HINT",
    "HISTORY_TOOL_OMISSION_TOKEN",
    "HistoryPayloadTooLarge",
    "compact_messages_for_llm_history",
    "compact_tool_details_in_text",
    "ensure_messages_within_budget",
    "estimate_messages_payload_bytes",
    "project_history_payload_stats",
]

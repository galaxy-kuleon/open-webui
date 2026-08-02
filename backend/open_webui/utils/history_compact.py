"""Budget-gated history compaction for LLM re-sends (M-U2 wall B).

Design (Codex CHANGES_REQUIRED 2026-08-03)
-----------------------------------------
* **LLM-facing only** — never rewrite stored DB/UI history.
* **No-op under budget** — when the fully assembled provider body fits, tool
  outputs must remain byte-identical (no global strip of every tool).
* **Selective when over budget** — drop oldest/largest *replayable reader*
  payloads only (read_file, vision, …), never short conversion/status receipts.
* **Fail closed** — indeterminate size measurement rejects; oversize after
  compact raises an actionable open-new-chat error before Hermes 413.

Provider limit (Hermes): 10_000_000 bytes. Soft compact trigger uses the same
named constant unless overridden.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any, Iterable

# ── named policy (single source) ─────────────────────────────────────────────

# Hermes gateway MAX_REQUEST_BYTES — fail-closed against this, not a fuzzy 8MB.
HERMES_MAX_REQUEST_BYTES = int(
    os.environ.get("HERMES_MAX_REQUEST_BYTES", str(10_000_000))
)
DEFAULT_MAX_LLM_MESSAGES_BYTES = int(
    os.environ.get("OWUI_MAX_LLM_MESSAGES_BYTES", str(HERMES_MAX_REQUEST_BYTES))
)

# Only these tools' large outputs are compactable when over budget.
# Single source for replayability policy (negative tests freeze this set).
REPLAYABLE_READER_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "vision_analyze",
        "search_files",
        "extract_document_text",
        "view_file",
        "view_knowledge_file",
        "query_knowledge_files",
    }
)

# Non-replayable tools must stay byte-identical under budget (negative tests).
# Listed for documentation; policy is "not in REPLAYABLE_READER_TOOLS".
NON_REPLAYABLE_TOOL_EXAMPLES: frozenset[str] = frozenset(
    {
        "submit_conversion",
        "conversion_status",
        "conversion_result",
        "mcp_soc_v2_submit_conversion",
        "mcp_soc_v2_conversion_status",
        "web_search",
        "terminal",
        "run_terminal_command",
    }
)

# When compacting a reader payload, keep this many leading characters as a stub.
READER_STUB_MAX_CHARS = int(
    os.environ.get("OWUI_HISTORY_READER_STUB_MAX_CHARS", "400")
)

TOOL_CALLS_DETAILS_RE = re.compile(
    r"<details\s+type=[\"']tool_calls[\"'][^>]*>.*?</details>\s*",
    re.DOTALL | re.IGNORECASE,
)
TOOL_CALLS_DETAILS_NAME_RE = re.compile(
    r"<details\s+type=[\"']tool_calls[\"']([^>]*)>(.*?)</details>\s*",
    re.DOTALL | re.IGNORECASE,
)
_NAME_ATTR_RE = re.compile(r"\bname=[\"']([^\"']+)[\"']", re.IGNORECASE)

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

# Mutation-sensitive wiring token — production must call this by name.
OUTBOUND_BUDGET_GATE_FN = "enforce_outbound_request_budget"


class HistoryPayloadTooLarge(Exception):
    """Raised when the outbound request still exceeds the provider budget."""

    def __init__(self, *, bytes_len: int, limit: int):
        self.bytes_len = bytes_len
        self.limit = limit
        super().__init__(HISTORY_OVERSIZE_USER_MESSAGE)


class PayloadSizeIndeterminate(Exception):
    """Raised when payload size cannot be measured — fail closed, never 0."""

    def __init__(self, cause: str = "serialize_failed"):
        self.cause = cause
        super().__init__(HISTORY_OVERSIZE_USER_MESSAGE)


def is_replayable_reader_tool(name: str | None) -> bool:
    if not name:
        return False
    base = str(name).strip()
    # MCP prefix: mcp_soc_v2_… is never a file reader here
    if base in REPLAYABLE_READER_TOOLS:
        return True
    # allow bare suffix match after last dot
    short = base.rsplit(".", 1)[-1]
    return short in REPLAYABLE_READER_TOOLS


def _omission_note(*, count: int, compressed: bool) -> str:
    flag = "compressed=true" if compressed else "compressed=false"
    return (
        f"[history: {count} {HISTORY_TOOL_OMISSION_TOKEN}(s) omitted from "
        f"re-sent context ({flag}). Final answers above remain. "
        f"{HISTORY_TOOL_OMISSION_HINT}.]"
    )


def estimate_messages_payload_bytes(messages: list[dict]) -> int:
    """JSON UTF-8 byte size of a messages list.

    Raises PayloadSizeIndeterminate on failure — never returns 0 for error
    (fail-open disease).
    """
    try:
        return len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PayloadSizeIndeterminate(str(type(exc).__name__)) from exc


def estimate_request_body_bytes(form_data: dict[str, Any]) -> int:
    """Estimate provider-bound chat-completions body size (fail closed)."""
    if not isinstance(form_data, dict):
        raise PayloadSizeIndeterminate("form_data_not_dict")
    # Keys that Hermes/OpenAI-compatible servers typically consume.
    body = {
        "model": form_data.get("model"),
        "messages": form_data.get("messages"),
        "tools": form_data.get("tools"),
        "tool_choice": form_data.get("tool_choice"),
        "stream": form_data.get("stream"),
        "temperature": form_data.get("temperature"),
        "max_tokens": form_data.get("max_tokens"),
        "top_p": form_data.get("top_p"),
    }
    # Drop Nones to avoid inflating with nulls the client may omit.
    body = {k: v for k, v in body.items() if v is not None}
    try:
        return len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise PayloadSizeIndeterminate(str(type(exc).__name__)) from exc


def _tool_name_map_from_messages(messages: list[dict]) -> dict[str, str]:
    """Map tool_call id → function name for structured messages."""
    mapping: dict[str, str] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tid = str(tc.get("id") or "")
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or tc.get("name") or "")
            if tid and name:
                mapping[tid] = name
        # OR-aligned output function_call items
        for item in msg.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                tid = str(item.get("call_id") or item.get("id") or "")
                name = str(item.get("name") or "")
                if tid and name:
                    mapping[tid] = name
    return mapping


def _details_name(attrs: str) -> str:
    m = _NAME_ATTR_RE.search(attrs or "")
    return m.group(1) if m else ""


def _stub_reader_text(original: str, *, tool_name: str) -> str:
    head = original[:READER_STUB_MAX_CHARS]
    return (
        f"{head}\n"
        f"...[replayable reader payload compacted: tool={tool_name}; "
        f"{max(0, len(original) - READER_STUB_MAX_CHARS)} chars omitted; "
        f"{HISTORY_TOOL_OMISSION_HINT}.]\n"
    )


def _compact_html_reader_details(text: str, *, force_all_readers: bool) -> tuple[str, int]:
    """Compact only details blocks whose name= is a replayable reader tool."""
    if not isinstance(text, str) or "tool_calls" not in text:
        return text if isinstance(text, str) else "", 0

    removed = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal removed
        attrs, body = match.group(1), match.group(2)
        name = _details_name(attrs)
        if not is_replayable_reader_tool(name):
            return match.group(0)  # preserve non-reader tools byte-identical
        if not force_all_readers and len(body) <= READER_STUB_MAX_CHARS:
            return match.group(0)
        removed += 1
        # Drop the whole block; prose outside remains.
        return ""

    compacted = TOOL_CALLS_DETAILS_NAME_RE.sub(repl, text)
    if removed:
        note = _omission_note(count=removed, compressed=True)
        compacted = compacted.rstrip()
        compacted = f"{compacted}\n\n{note}\n" if compacted else f"{note}\n"
    return compacted, removed


def _message_tool_name(message: dict, id_map: dict[str, str]) -> str:
    if message.get("name"):
        return str(message.get("name"))
    tid = str(message.get("tool_call_id") or message.get("tool_callId") or "")
    return id_map.get(tid, "")


def _payload_size_of_message(message: dict) -> int:
    try:
        return len(json.dumps(message, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def compact_messages_for_llm_history(
    messages: list[dict],
    *,
    limit_bytes: int | None = None,
    force: bool = False,
) -> list[dict]:
    """Deep-copy messages; compact only when over *limit_bytes* (or force).

    Under budget and not forced → structural deepcopy only; tool payloads stay
    byte-identical. Over budget → remove/stub largest replayable reader
    payloads until under limit or nothing left to compact.
    """
    if not isinstance(messages, list):
        return messages

    limit = DEFAULT_MAX_LLM_MESSAGES_BYTES if limit_bytes is None else int(limit_bytes)
    working = deepcopy(messages)

    try:
        size = estimate_messages_payload_bytes(working)
    except PayloadSizeIndeterminate:
        raise

    if not force and size <= limit:
        return working

    id_map = _tool_name_map_from_messages(working)

    # Pass 1: compact HTML reader details in assistant content/output (largest first
    # by rewriting all reader blocks when forced/over budget).
    for msg in working:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and "tool_calls" in content:
            msg["content"], _ = _compact_html_reader_details(
                content, force_all_readers=True
            )
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    part = dict(part)
                    text = part.get("text")
                    if isinstance(text, str) and "tool_calls" in text:
                        part["text"], _ = _compact_html_reader_details(
                            text, force_all_readers=True
                        )
                    new_parts.append(part)
                else:
                    new_parts.append(part)
            msg["content"] = new_parts

        if isinstance(msg.get("output"), list):
            new_out = []
            for item in msg["output"]:
                if not isinstance(item, dict):
                    new_out.append(item)
                    continue
                item = dict(item)
                if item.get("type") == "message" and isinstance(item.get("content"), list):
                    parts = []
                    for part in item["content"]:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            part = dict(part)
                            if "tool_calls" in part["text"]:
                                part["text"], _ = _compact_html_reader_details(
                                    part["text"], force_all_readers=True
                                )
                        parts.append(part)
                    item["content"] = parts
                elif item.get("type") == "function_call_output":
                    name = id_map.get(str(item.get("call_id") or ""), "")
                    if is_replayable_reader_tool(name):
                        outs = item.get("output")
                        if isinstance(outs, list):
                            no = []
                            for part in outs:
                                if isinstance(part, dict) and isinstance(
                                    part.get("text"), str
                                ):
                                    part = dict(part)
                                    if len(part["text"]) > READER_STUB_MAX_CHARS:
                                        part["text"] = _stub_reader_text(
                                            part["text"], tool_name=name or "reader"
                                        )
                                no.append(part)
                            item["output"] = no
                        elif isinstance(outs, str) and len(outs) > READER_STUB_MAX_CHARS:
                            item["output"] = _stub_reader_text(
                                outs, tool_name=name or "reader"
                            )
                new_out.append(item)
            msg["output"] = new_out

        role = msg.get("role")
        if role in {"tool", "function"} and isinstance(msg.get("content"), str):
            name = _message_tool_name(msg, id_map)
            if is_replayable_reader_tool(name) and len(msg["content"]) > READER_STUB_MAX_CHARS:
                msg["content"] = _stub_reader_text(msg["content"], tool_name=name)

    return working


def ensure_messages_within_budget(
    messages: list[dict],
    *,
    limit_bytes: int | None = None,
) -> list[dict]:
    """Compact if needed; raise HistoryPayloadTooLarge if still over limit."""
    limit = DEFAULT_MAX_LLM_MESSAGES_BYTES if limit_bytes is None else int(limit_bytes)
    try:
        size = estimate_messages_payload_bytes(messages if isinstance(messages, list) else [])
    except PayloadSizeIndeterminate as exc:
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit) from exc

    if size <= limit:
        # Under budget: still return a deepcopy so callers cannot mutate storage,
        # but tool payloads remain byte-identical to input.
        return deepcopy(messages) if isinstance(messages, list) else messages

    compacted = compact_messages_for_llm_history(messages, limit_bytes=limit, force=True)
    try:
        after = estimate_messages_payload_bytes(compacted)
    except PayloadSizeIndeterminate as exc:
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit) from exc
    if after > limit:
        raise HistoryPayloadTooLarge(bytes_len=after, limit=limit)
    return compacted


def enforce_outbound_request_budget(
    form_data: dict[str, Any],
    *,
    limit_bytes: int | None = None,
) -> dict[str, Any]:
    """Fail-closed gate on the provider-bound body after final assembly.

    1. If body over limit, selectively compact messages (reader payloads only).
    2. Re-measure full body; if still over or indeterminate → raise.
    """
    limit = HERMES_MAX_REQUEST_BYTES if limit_bytes is None else int(limit_bytes)
    if not isinstance(form_data, dict):
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit)

    messages = form_data.get("messages")
    if not isinstance(messages, list):
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit)

    try:
        body_size = estimate_request_body_bytes(form_data)
    except PayloadSizeIndeterminate as exc:
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit) from exc

    if body_size <= limit:
        return form_data

    # Compact messages only, then re-estimate the full body.
    form_data = dict(form_data)
    form_data["messages"] = compact_messages_for_llm_history(
        messages, limit_bytes=limit, force=True
    )
    try:
        after = estimate_request_body_bytes(form_data)
    except PayloadSizeIndeterminate as exc:
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit) from exc
    if after > limit:
        raise HistoryPayloadTooLarge(bytes_len=after, limit=limit)
    return form_data


def apply_outbound_history_controls(
    form_data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    *,
    limit_bytes: int | None = None,
) -> dict[str, Any]:
    """Production orchestration for M-U2 (testable pure core).

    Order (critical for BLOCKING-1 + BLOCKING-3):
      1. Observe reader outcomes + build coverage from *pre-compact* messages
         so facts survive stubbing.
      2. Fail-closed enforce_outbound_request_budget on fully assembled body.
      3. Inject bounded coverage summary into last user message.
      4. Re-gate after the (small) injection.
      5. Persist coverage on metadata (mechanism, not model prose).

    Middleware must call this after all process_chat_payload injections.
    Mutation removing that call must red ProductionWiringMutationTests.
    """
    # Local import avoids circular import at module load (read_coverage → hc).
    from open_webui.utils.read_coverage import (
        build_coverage_for_request,
        format_coverage_summary,
        inject_coverage_into_messages,
    )

    if not isinstance(form_data, dict):
        raise HistoryPayloadTooLarge(bytes_len=-1, limit=limit_bytes or HERMES_MAX_REQUEST_BYTES)

    meta = metadata if isinstance(metadata, dict) else {}

    try:
        before = estimate_request_body_bytes(form_data)
    except PayloadSizeIndeterminate:
        before = -1

    # Coverage from pre-compact messages (reader facts must not depend on stubs).
    pre_coverage = build_coverage_for_request(form_data, meta, compressed=False)

    form_data = enforce_outbound_request_budget(form_data, limit_bytes=limit_bytes)

    try:
        after = estimate_request_body_bytes(form_data)
    except PayloadSizeIndeterminate:
        after = before
    compressed = before > 0 and after >= 0 and after < before

    coverage = dict(pre_coverage)
    coverage["compressed"] = bool(compressed)
    summary = format_coverage_summary(coverage)
    if summary:
        form_data = dict(form_data)
        form_data["messages"] = inject_coverage_into_messages(
            form_data.get("messages") or [],
            summary,
        )
    if isinstance(metadata, dict):
        metadata["read_coverage"] = coverage
        metadata["history_compressed"] = bool(compressed)

    # Re-gate after injecting the (small) coverage summary.
    form_data = enforce_outbound_request_budget(form_data, limit_bytes=limit_bytes)
    return form_data


def project_history_payload_stats(
    messages: list[dict],
    *,
    limit_bytes: int | None = None,
) -> dict[str, Any]:
    """Before/after stats using the *same* compact policy as production.

    Numbers are directional for stored-tool-dump weight on a linear message
    list — not a guarantee of post-injection outbound Hermes bytes.
    """
    limit = DEFAULT_MAX_LLM_MESSAGES_BYTES if limit_bytes is None else int(limit_bytes)
    before = estimate_messages_payload_bytes(messages)
    try:
        compacted = compact_messages_for_llm_history(
            messages, limit_bytes=limit, force=before > limit
        )
        after = estimate_messages_payload_bytes(compacted)
    except PayloadSizeIndeterminate:
        after = before
    return {
        "bytes_before": before,
        "bytes_after": after,
        "bytes_saved": max(0, before - after),
        "over_limit_before": before > limit,
        "over_limit_after": after > limit,
        "limit_bytes": limit,
        "estimate_kind": "messages_json_linear_not_final_http_body",
    }


# Back-compat alias used by older tests / baseline
DEFAULT_TOOL_RESULT_MAX_CHARS = READER_STUB_MAX_CHARS


def compact_tool_details_in_text(text: str) -> tuple[str, int]:
    """Legacy helper: compact reader-named HTML details only."""
    return _compact_html_reader_details(text, force_all_readers=True)


__all__ = [
    "DEFAULT_MAX_LLM_MESSAGES_BYTES",
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "HERMES_MAX_REQUEST_BYTES",
    "HISTORY_OVERSIZE_USER_MESSAGE",
    "HISTORY_TOOL_OMISSION_HINT",
    "HISTORY_TOOL_OMISSION_TOKEN",
    "NON_REPLAYABLE_TOOL_EXAMPLES",
    "OUTBOUND_BUDGET_GATE_FN",
    "REPLAYABLE_READER_TOOLS",
    "HistoryPayloadTooLarge",
    "PayloadSizeIndeterminate",
    "apply_outbound_history_controls",
    "compact_messages_for_llm_history",
    "compact_tool_details_in_text",
    "enforce_outbound_request_budget",
    "ensure_messages_within_budget",
    "estimate_messages_payload_bytes",
    "estimate_request_body_bytes",
    "is_replayable_reader_tool",
    "project_history_payload_stats",
]

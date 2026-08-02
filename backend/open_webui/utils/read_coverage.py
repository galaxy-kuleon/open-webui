"""Mechanism-produced per-file read coverage (M-U2 BLOCKING-1 / C4).

Compares observed reader outcomes against the immutable attachment/handoff
manifest. Model prose is never the source of truth.

User-visible minimum (Kimi HEMKV):
  1. three numbers: attached, read, failed with attached == read + failed
     (partial counts as read for the equality when reporting "consumed";
      we also expose partial separately)
  2. each failed named by handle (Fxx)
  3. compression flag
  4. at least one independently checkable fact per file when available
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from open_webui.utils.history_compact import (
    REPLAYABLE_READER_TOOLS,
    is_replayable_reader_tool,
)

# Stable tokens for mutation tests / runner probes.
COVERAGE_SUMMARY_BEGIN = "<!-- owui-read-coverage -->"
COVERAGE_SUMMARY_END = "<!-- /owui-read-coverage -->"
COVERAGE_SUMMARY_TITLE = "## Read coverage (authoritative)"

_HANDLE_RE = re.compile(r"\b(F\d{1,4})\b", re.IGNORECASE)
# Synthetic canaries used by HEMKV runner / lab fixtures.
_CANARY_RE = re.compile(
    r"(CANARY[-_][A-Za-z0-9._-]{4,64}|ALPHA[-_][A-Za-z0-9._-]{3,64})",
    re.IGNORECASE,
)
_DETAILS_RE = re.compile(
    r"<details\s+type=[\"']tool_calls[\"']([^>]*)>(.*?)</details>",
    re.DOTALL | re.IGNORECASE,
)
_NAME_ATTR_RE = re.compile(r"\bname=[\"']([^\"']+)[\"']", re.IGNORECASE)
_ARGS_ATTR_RE = re.compile(r"\barguments=[\"']([^\"']*)[\"']", re.IGNORECASE)


def _display_handle_name(path_or_name: str) -> str:
    base = str(path_or_name).rsplit("/", 1)[-1]
    # Strip Path-B ordinal-nonce prefix when present.
    return re.sub(r"^\d{3}-[0-9a-f]{8}-", "", base, count=1) or base


def extract_attachment_handles(form_data: dict | None, metadata: dict | None) -> list[dict[str, str]]:
    """Build immutable attachment rows [{id, name}] from request metadata/files."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(handle: str, name: str) -> None:
        h = handle.upper() if handle else f"A{len(rows)+1:02d}"
        if h in seen:
            return
        seen.add(h)
        rows.append({"id": h, "name": _display_handle_name(name or h)})

    meta = metadata if isinstance(metadata, dict) else {}
    # Explicit handle map if present (Hermes grant style).
    handles = meta.get("file_handles") or meta.get("attachment_handles")
    if isinstance(handles, dict):
        for h, path in handles.items():
            add(str(h), str(path))
    # metadata.files / form files
    files = []
    if isinstance(meta.get("files"), list):
        files.extend(meta["files"])
    if isinstance(form_data, dict) and isinstance(form_data.get("files"), list):
        files.extend(form_data["files"])
    idx = 1
    for f in files:
        if not isinstance(f, dict):
            continue
        name = f.get("name") or f.get("filename") or f.get("id") or f"file{idx}"
        # Prefer declared handle
        hid = f.get("handle") or f.get("alias")
        if not hid:
            hid = f"F{idx:02d}"
            idx += 1
        add(str(hid), str(name))
    # <files> block in last user message (Path B)
    if isinstance(form_data, dict):
        for msg in reversed(form_data.get("messages") or []):
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            content = msg.get("content")
            text = content if isinstance(content, str) else ""
            if isinstance(content, list):
                text = " ".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            if "<files>" not in text and "<file " not in text:
                break
            # Count file tags for handles F01..
            n = len(re.findall(r"<file\b", text, flags=re.I))
            if n and not rows:
                for i in range(1, n + 1):
                    add(f"F{i:02d}", f"attachment-{i}")
            break
    return rows


def _fact_from_text(text: str) -> str:
    if not text:
        return ""
    m = _CANARY_RE.search(text)
    if m:
        return m.group(1)
    # Short privacy-safe fingerprint of first non-empty line
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 8:
            return s[:80]
    return text.strip()[:80]


def _handles_in_text(text: str) -> list[str]:
    return [h.upper() for h in _HANDLE_RE.findall(text or "")]


def _parse_tool_result_status(text: str) -> tuple[str, str]:
    """Return (status, fact) from a tool result body."""
    fact = _fact_from_text(text)
    if not text:
        return "failed", fact
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if data.get("error") or data.get("extraction_failed"):
                return "failed", fact or str(data.get("error") or "error")[:80]
            report = str(data.get("report_as") or data.get("coverage") or "")
            if report == "partial" or data.get("truncated"):
                return "partial", fact or str(data.get("content") or "")[:80]
            if data.get("content") is not None or data.get("extracted_document"):
                return "complete", fact or str(data.get("content") or "")[:80]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    if "error" in text.lower()[:200] and "cannot" in text.lower()[:200]:
        return "failed", fact
    return "complete", fact


def observe_reader_outcomes(messages: list[dict]) -> dict[str, dict[str, Any]]:
    """Scan LLM-facing messages for reader tool results keyed by handle."""
    outcomes: dict[str, dict[str, Any]] = {}
    id_to_name: dict[str, str] = {}
    id_to_handles: dict[str, list[str]] = {}

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tid = str(tc.get("id") or "")
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or "")
            args = str(fn.get("arguments") or "")
            if tid:
                id_to_name[tid] = name
                id_to_handles[tid] = _handles_in_text(args)
        for item in msg.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                tid = str(item.get("call_id") or "")
                name = str(item.get("name") or "")
                args = str(item.get("arguments") or "")
                if tid:
                    id_to_name[tid] = name
                    id_to_handles[tid] = _handles_in_text(args)

    def record(handle: str, *, status: str, reader: str, fact: str, extent: str) -> None:
        h = handle.upper()
        prev = outcomes.get(h)
        # Prefer complete > partial > failed for upgrades; never erase failed if only pending
        rank = {"failed": 0, "partial": 1, "complete": 2}
        if prev and rank.get(prev.get("status"), -1) > rank.get(status, -1):
            return
        outcomes[h] = {
            "status": status,
            "reader": reader,
            "fact": (fact or "")[:120],
            "extent": extent,
        }

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        # Structured tool results
        if role in {"tool", "function"}:
            tid = str(msg.get("tool_call_id") or "")
            name = str(msg.get("name") or id_to_name.get(tid) or "")
            if not is_replayable_reader_tool(name):
                continue
            content = msg.get("content")
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            status, fact = _parse_tool_result_status(text)
            handles = id_to_handles.get(tid) or _handles_in_text(text)
            if not handles:
                handles = [f"R{len(outcomes)+1:02d}"]  # anonymous reader hit
            for h in handles:
                record(
                    h,
                    status=status,
                    reader=name,
                    fact=fact,
                    extent=f"chars={len(text)}",
                )
        # HTML details in assistant content
        content = msg.get("content")
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    texts.append(str(p.get("text") or ""))
        for text in texts:
            if "tool_calls" not in text:
                continue
            for m in _DETAILS_RE.finditer(text):
                attrs, body = m.group(1), m.group(2)
                nm = _NAME_ATTR_RE.search(attrs or "")
                name = nm.group(1) if nm else ""
                if not is_replayable_reader_tool(name):
                    continue
                args_m = _ARGS_ATTR_RE.search(attrs or "")
                args = args_m.group(1) if args_m else ""
                # HTML-escaped JSON in attributes — best-effort handle extract
                handles = _handles_in_text(args) or _handles_in_text(body)
                status, fact = _parse_tool_result_status(body)
                if not handles:
                    handles = [f"R{len(outcomes)+1:02d}"]
                for h in handles:
                    record(
                        h,
                        status=status,
                        reader=name,
                        fact=fact,
                        extent=f"chars={len(body)}",
                    )
    return outcomes


def build_read_coverage_manifest(
    *,
    attachments: list[dict[str, str]],
    outcomes: dict[str, dict[str, Any]],
    compressed: bool = False,
) -> dict[str, Any]:
    """Build authoritative coverage compared to attachment manifest."""
    files: list[dict[str, Any]] = []
    read_complete = 0
    read_partial = 0
    failed = 0
    unread = 0
    for att in attachments:
        hid = str(att.get("id") or "").upper()
        name = att.get("name") or hid
        obs = outcomes.get(hid) or {}
        status = obs.get("status") or "unread"
        if status == "complete":
            read_complete += 1
            bucket = "read_complete"
        elif status == "partial":
            read_partial += 1
            bucket = "read_partial"
        elif status == "failed":
            failed += 1
            bucket = "failed"
        else:
            unread += 1
            bucket = "unread"
            status = "unread"
        files.append(
            {
                "id": hid,
                "name": name,
                "status": status,
                "reader": obs.get("reader") or "",
                "extent": obs.get("extent") or "",
                "fact": obs.get("fact") or "",
                "bucket": bucket,
            }
        )

    # Orphan reader hits (handle not in manifest) still count as observed
    for hid, obs in outcomes.items():
        if any(f["id"] == hid for f in files):
            continue
        status = obs.get("status") or "complete"
        if status == "complete":
            read_complete += 1
        elif status == "partial":
            read_partial += 1
        elif status == "failed":
            failed += 1
        files.append(
            {
                "id": hid,
                "name": hid,
                "status": status,
                "reader": obs.get("reader") or "",
                "extent": obs.get("extent") or "",
                "fact": obs.get("fact") or "",
                "bucket": f"orphan_{status}",
            }
        )

    attached = len(attachments)
    read = read_complete + read_partial
    # Kimi equality: attached == read + failed  (unread treated as failed for the identity)
    failed_or_unread = failed + unread
    return {
        "attached": attached,
        "read": read,
        "read_complete": read_complete,
        "read_partial": read_partial,
        "failed": failed_or_unread,
        "failed_hard": failed,
        "unread": unread,
        "compressed": bool(compressed),
        "files": files,
        "identity_ok": attached == read + failed_or_unread,
        "replayable_reader_tools": sorted(REPLAYABLE_READER_TOOLS),
    }


def format_coverage_summary(manifest: dict[str, Any]) -> str:
    """Bounded user/model-visible coverage block."""
    if not manifest or not manifest.get("attached"):
        return ""
    lines = [
        "",
        COVERAGE_SUMMARY_BEGIN,
        COVERAGE_SUMMARY_TITLE,
        "",
        f"- attached: **{manifest['attached']}**",
        f"- read: **{manifest['read']}** "
        f"(complete={manifest['read_complete']}, partial={manifest['read_partial']})",
        f"- failed/unreadable: **{manifest['failed']}**",
        f"- compressed: **{str(bool(manifest.get('compressed'))).lower()}**",
    ]
    if manifest.get("compressed"):
        lines.append(
            "- note: early files may be post-compression highlights, not full text."
        )
    if not manifest.get("identity_ok"):
        lines.append(
            "- warning: attached ≠ read + failed — coverage accounting incomplete."
        )
    failed_ids = [
        f["id"]
        for f in manifest.get("files") or []
        if f.get("status") in {"failed", "unread"}
    ]
    if failed_ids:
        lines.append("- failed handles: " + ", ".join(f"`{h}`" for h in failed_ids[:50]))
    lines.append("- per-file facts (spot-check):")
    for f in (manifest.get("files") or [])[:80]:
        fact = f.get("fact") or "(no extractable fact)"
        lines.append(
            f"  - `{f['id']}` status={f.get('status')} reader={f.get('reader') or '-'} "
            f"extent={f.get('extent') or '-'} fact={fact}"
        )
    lines.extend([COVERAGE_SUMMARY_END, ""])
    return "\n".join(lines)


def build_coverage_for_request(
    form_data: dict,
    metadata: dict | None,
    *,
    compressed: bool = False,
) -> dict[str, Any]:
    attachments = extract_attachment_handles(form_data, metadata)
    messages = form_data.get("messages") if isinstance(form_data, dict) else []
    outcomes = observe_reader_outcomes(messages if isinstance(messages, list) else [])
    return build_read_coverage_manifest(
        attachments=attachments,
        outcomes=outcomes,
        compressed=compressed,
    )


def inject_coverage_into_messages(
    messages: list[dict],
    summary: str,
) -> list[dict]:
    """Append coverage summary to the last user message (mechanism, not model)."""
    if not summary or not isinstance(messages, list) or not messages:
        return messages
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        msg = out[i]
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        msg = dict(msg)
        content = msg.get("content")
        if isinstance(content, str):
            if COVERAGE_SUMMARY_BEGIN in content:
                return out
            msg["content"] = content.rstrip() + "\n\n" + summary
        elif isinstance(content, list):
            parts = list(content)
            parts.append({"type": "text", "text": summary})
            msg["content"] = parts
        else:
            msg["content"] = summary
        out[i] = msg
        break
    return out


__all__ = [
    "COVERAGE_SUMMARY_BEGIN",
    "COVERAGE_SUMMARY_END",
    "COVERAGE_SUMMARY_TITLE",
    "build_coverage_for_request",
    "build_read_coverage_manifest",
    "extract_attachment_handles",
    "format_coverage_summary",
    "inject_coverage_into_messages",
    "observe_reader_outcomes",
]

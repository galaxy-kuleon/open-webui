"""Terminal-state resolution for SOC / SOCv2 same-origin export directories.

The MCP reconciler materializes DOCX under ``/handoff/exports/<prefix>/<nonce>/``
and settles non-success jobs with a co-located ``.failed`` marker (timeout /
needs_attention / failed / canceled). The OpenWebUI export route uses this
helper so users clicking chat links always see an honest terminal state.

States
------
* ``done`` — ``.done`` present (DOCX should be served by the route when the
  requested path is an existing file)
* ``failed`` — ``.failed`` present; ``detail`` is the marker text (timeout
  reason, etc.), never a filename
* ``pending`` — directory exists, no terminal marker yet
* ``absent`` — no artifact directory (unknown / wrong link)

Mutation note: removing marker writes in the MCP reconciler must make the
failed/timeout unit tests red — those tests create fixtures with only
``.failed`` and assert this helper returns ``failed``.
"""

from __future__ import annotations

from pathlib import Path

DONE_MARKER = ".done"
FAILED_MARKER = ".failed"
MAX_FAILED_DETAIL_CHARS = 400

# Named terminal reasons we surface (refutable).
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_PENDING = "pending"
STATE_ABSENT = "absent"


def read_failed_detail(art_dir: Path) -> str:
    """Read ``.failed`` marker text; empty string if missing/unreadable."""
    marker = art_dir / FAILED_MARKER
    try:
        if not marker.is_file():
            return ""
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        return text[:MAX_FAILED_DETAIL_CHARS]
    except OSError:
        return ""


def resolve_artifact_state(art_dir: Path) -> tuple[str, str]:
    """Return ``(state, detail)`` for a SOC export nonce directory.

    Priority: failed > done > pending > absent.
    A directory with both markers is treated as failed (honest fail-closed).
    """
    try:
        if not art_dir.is_dir():
            return STATE_ABSENT, ""
    except OSError:
        return STATE_ABSENT, ""

    detail = read_failed_detail(art_dir)
    if detail or (art_dir / FAILED_MARKER).is_file():
        # Marker exists even if empty → still failed (settled terminal).
        if not detail:
            detail = "The conversion did not succeed. You can try again."
        return STATE_FAILED, detail

    try:
        if (art_dir / DONE_MARKER).is_file():
            return STATE_DONE, ""
    except OSError:
        return STATE_ABSENT, ""

    return STATE_PENDING, ""


def failed_user_message(detail: str) -> str:
    """Human-facing body for a failed export (bilingual, actionable)."""
    reason = (detail or "").strip() or "The conversion did not succeed."
    return (
        f"{reason}\n\n"
        "請回到聊天視窗，直接請 AI「重新轉檔」即可 — 不需要重新上傳檔案。"
        " Go back to the chat and ask the AI to retry the conversion "
        "(no re-upload needed)."
    )

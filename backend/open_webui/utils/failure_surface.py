"""#16 runtime slice (backend half): surface silent empty/interrupted assistant turns.

A turn that finalizes (or is cancelled) with NO rendered content is OpenWebUI's
trust-breaking "false success": ``ResponseMessage`` shows a *completed* bubble with nothing
in it (no Skeleton, no content, no error — see ResponseMessage.svelte:879/881/922). This
module is a PURE detector + a privacy-safe error/trace builder so ``middleware`` can mark
such a turn with a visible error state instead of leaving it blank.

Grilled design decisions (#16):
  * Detection is keyed on ``serialize_output(output)`` — the SAME rendered HTML the user
    sees (middleware.py:446) — so a legitimate tool / skip-rag turn, which renders
    tool/function blocks, is non-empty and does NOT flag; only a truly-blank turn flags.
  * At the backend finalizer the stream has ALREADY ended by construction, so there is no
    "actively streaming" false-positive; we still accept ``task_active`` for callers/reuse.
  * A5: a cancelled turn that kept partial content has non-empty ``serialize_output`` →
    no flag → the partial is left intact for the frontend task to surface.

Privacy (M3 / M4): the payload that crosses into the persisted chat / emitted event carries
ONLY a bucketed cause label + an opaque, server-generated trace id — NEVER
``serialize_output()`` / raw content / titles / names. The cause is the DB-signal-derivable
bucket (HONEST: not a full root cause; the slice-1 triage tool resolves that later, located
via the trace id). This module deliberately has NO ``open_webui`` imports so it stays pure
and unit-testable with no live stack (load it by file path in tests).

Single-sourcing note: ``CAUSE_EMPTY_FINALIZED`` mirrors
``scripts/ops/openwebui_8083_chat_triage.py`` ``CAUSES`` — ``db_stream_flush`` = "task ended
but the final assistant message is empty/done=false". The owui container cannot import the
parent-repo ops script, so the label is duplicated here by hand; keep the two in sync.
"""

from __future__ import annotations

# Bucketed, DB-signal-derivable cause label (mirrors chat_triage CAUSES['db_stream_flush']).
CAUSE_EMPTY_FINALIZED = "db_stream_flush"

# The CANONICAL set of cause labels this slice is permitted to emit. It is a subset/alias of
# scripts/ops/openwebui_8083_chat_triage.py ``CAUSES``. It is enforced FAIL-LOUD in
# ``build_error_payload`` / ``assert_privacy_safe`` so an arbitrary or raw string can NEVER
# reach ``error.cause`` or the rendered banner (M4). Single-sourcing against drift is
# additionally enforced at TEST time (``test_failure_surface`` cross-checks this set against
# ``triage.CAUSES`` and fails on divergence) — the owui container cannot import the
# parent-repo ops script, so an import-time cross-check here is not possible.
ALLOWED_CAUSES = frozenset({CAUSE_EMPTY_FINALIZED})

# The ONLY keys permitted in an assistant-message ``error`` payload that crosses the
# privacy boundary (M4). Anything else is a fail-loud bug.
ERROR_PAYLOAD_ALLOWED_KEYS = frozenset({"content", "cause", "trace_id"})


def should_flag_empty(rendered_content: str, done: bool, task_active: bool) -> bool:
    """Pure: ``True`` iff a finalized/cancelled turn rendered to NOTHING and is not still live.

    ``rendered_content`` MUST be ``serialize_output(output)`` (the rendered HTML the user
    sees), so tool/skip-rag turns are non-empty and do not flag. Flag when rendered-empty
    AND (``done`` is True OR the task is no longer active). At the backend finalizer the
    stream has ended, so this never fires on an in-progress stream.
    """
    if (rendered_content or "").strip():
        return False
    return bool(done) or (not task_active)


def make_trace_id(chat_id: str, message_id: str) -> str:
    """Opaque, server-generated trace id ``t-<chatid8>-<msgid8>`` — only ids, never content.

    Generated at finalization so the id written to the message error == the id logged, so
    ops can paste it → grep logs → run the slice-1 triage tool on that chat (closes L3).
    """
    return "t-{}-{}".format((chat_id or "")[:8], (message_id or "")[:8])


def build_error_payload(
    chat_id: str,
    message_id: str,
    cause: str = CAUSE_EMPTY_FINALIZED,
) -> dict:
    """Build the privacy-safe assistant-message ``error`` payload.

    Returns ``{"content", "cause", "trace_id"}`` where ``content`` is a fixed banner composed
    ONLY from the cause label + opaque trace id (this is what ResponseMessage's ``<Error>``
    renders), and ``cause`` / ``trace_id`` are structured extras for the later frontend rich
    state + ops. It takes NO content argument by design, so raw chat content can never enter
    it; the ``cause`` is FAIL-LOUD validated against ``ALLOWED_CAUSES`` (an arbitrary/raw
    string raises and can never reach ``error.cause`` or the banner); ``assert_privacy_safe``
    re-validates the result.
    """
    if cause not in ALLOWED_CAUSES:
        raise ValueError(
            "non-canonical cause {!r}; allowed: {}".format(cause, sorted(ALLOWED_CAUSES))
        )
    trace_id = make_trace_id(chat_id, message_id)
    payload = {"content": build_banner(cause, trace_id), "cause": cause, "trace_id": trace_id}
    assert_privacy_safe(payload)
    return payload


def assert_privacy_safe(payload: dict) -> dict:
    """Fail-loud (M4): the error payload may contain ONLY the allow-listed keys, all string
    values, and its rendered ``content`` may reference nothing beyond the cause label + trace
    id (a guard against a future edit interpolating raw content/title/names into the banner).
    """
    extra = set(payload) - ERROR_PAYLOAD_ALLOWED_KEYS
    if extra:
        raise ValueError("error payload has non-allow-listed keys: {}".format(sorted(extra)))
    for key, val in payload.items():
        if not isinstance(val, str):
            raise ValueError(
                "error payload field '{}' must be str, got {}".format(key, type(val).__name__)
            )
    # The cause must be a CANONICAL label (never an arbitrary/raw string).
    cause = payload.get("cause", "")
    if cause not in ALLOWED_CAUSES:
        raise ValueError(
            "error payload 'cause' {!r} is not canonical; allowed: {}".format(cause, sorted(ALLOWED_CAUSES))
        )
    # The banner must be reconstructible from ONLY the cause + trace id + fixed boilerplate.
    trace_id = payload.get("trace_id", "")
    rebuilt = build_banner(cause, trace_id)
    if payload.get("content") != rebuilt:
        raise ValueError("error payload 'content' is not the fixed cause+trace banner (possible leak)")
    return payload


def build_banner(cause: str, trace_id: str) -> str:
    """The single source of the visible error banner text (cause + trace id only)."""
    return (
        "This response finished without any content (cause: {}). "
        "Nothing was delivered — retry, or share trace {} with ops."
    ).format(cause, trace_id)

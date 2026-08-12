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

Observability (added 2026-08-12, after a live miss): this module also OWNS the log
marker for the event, and it is the only thing this module ever logs. Two real users
hit ``db_stream_flush`` on a direct (non-hermes) model, so no gateway code was in the
path and no gateway marker could fire; ``middleware`` logged the trace id as free-form
JSON, which the journey ledger cannot parse, and the container was recreated three hours
later. The banner had told those users to "share trace t-… with ops" — and ops, meaning
me, then had nothing to grep. A promise the system cannot keep is worse than no promise.

Why the emitter lives HERE and not in ``middleware``: ``journey_ledger`` trusts a logger,
not a service. ``open_webui.utils.middleware`` is trusted COUNT-ONLY — it may say that a
turn ended badly, never whose — because that module also logs provider text and exception
strings, so a crafted upstream message beginning with our marker could inject ``chat=``
and name a victim. This module logs NOTHING but the closed-vocabulary marker below, so it
has no such channel, and the ledger can trust it with the trace/chat ids that make the
banner's promise keepable. That property is mechanical, not a comment: ``journey_ledger``
self-test parses this file and fails if any second logging call ever appears in it.
"""

from __future__ import annotations

import logging

# The one logger this module owns.
#
# The NAME here is very nearly decorative, and knowing that matters. Measured
# 2026-08-12 in a disposable container off the running image
# (`scripts/ops/owui_marker_wire_probe.sh`): owui's `InterceptHandler` walks to the
# CALLER'S FRAME and never passes `record.name`, so the logger printed on the line the
# collector reads is this MODULE's name, whatever string is passed below. Renaming it to
# `totally.unrelated.name` still emitted `open_webui.utils.failure_surface`.
#
# So the ledger's identity trust is really "this code lives in THIS FILE", which is why a
# second logging call here is the hazard and why another module calling
# `getLogger("open_webui.utils.failure_surface")` cannot borrow the trust -- also measured,
# as control 3 of that probe. The name is kept accurate anyway: the unit tests attach a
# handler by it, and a wrong one would be a lie to the next reader.
_MARKER_LOG = logging.getLogger("open_webui.utils.failure_surface")

# The ledger's stream-marker vocabulary. `empty_reply` is the gateway's existing kind for
# "the turn produced nothing"; an owui-side empty turn is the same fact observed at the
# only layer that can see it when the gateway is not in the path.
MARKER_EMPTY_REPLY = "empty_reply"

# A turn can finalize empty on two paths, and they mean different things to whoever reads
# the ledger: `finalized` = the stream ended by itself with nothing rendered; `interrupted`
# = it was cancelled (user stop, deploy, disconnect) before anything was rendered.
PHASE_FINALIZED = "finalized"
PHASE_INTERRUPTED = "interrupted"
ALLOWED_PHASES = frozenset({PHASE_FINALIZED, PHASE_INTERRUPTED})

# Whether the BANNER reached the user, which is a different fact from whether the
# turn was blank. A blank turn that explained itself and a blank turn that said
# nothing are different failures to whoever reads the report, and only the caller
# knows which happened -- the interrupted path writes the error inside a try/except,
# so "we surfaced it" is an outcome that may not have occurred. The reader must not
# have to infer this from the service name.
NOTICE_WRITTEN = "written"
NOTICE_UNDELIVERED = "undelivered"
ALLOWED_NOTICES = frozenset({NOTICE_WRITTEN, NOTICE_UNDELIVERED})

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


def build_marker(payload: dict, phase: str, chat_id: str, message_id: str,
                 notice: str) -> str:
    """The ledger line for an empty turn: closed vocabulary, ids only, no content.

    Separated from ``log_empty_turn`` so a test can assert the exact bytes without a
    logging handler, and so the ledger's parser can be exercised against the real
    producer's output rather than a hand-copied string.
    """
    if phase not in ALLOWED_PHASES:
        raise ValueError("phase {!r} is not canonical; allowed: {}".format(phase, sorted(ALLOWED_PHASES)))
    if notice not in ALLOWED_NOTICES:
        raise ValueError("notice {!r} is not canonical; allowed: {}".format(notice, sorted(ALLOWED_NOTICES)))
    assert_privacy_safe(payload)
    # `_KV_RE` in journey_ledger splits on whitespace, so a value containing a space would
    # silently truncate the record. Every value here is an opaque id or a label from a
    # closed set, and this states that rather than assuming it.
    for key, val in (("cause", payload["cause"]), ("trace", payload["trace_id"]),
                     ("chat", chat_id or ""), ("msg", message_id or "")):
        if val != "".join(val.split()):
            raise ValueError("marker field {!r} contains whitespace: {!r}".format(key, val))
    return (
        "{} service=owui phase={} notice={} cause={} trace={} chat={} msg={}".format(
            MARKER_EMPTY_REPLY, phase, notice, payload["cause"], payload["trace_id"],
            chat_id or "-", message_id or "-",
        )
    )


def log_empty_turn(payload: dict, phase: str, chat_id: str, message_id: str,
                   notice: str) -> str:
    """Emit the empty-turn marker. THE ONLY LOGGING CALL IN THIS MODULE -- see the module
    docstring: the ledger's identity trust in this logger rests on that being true, and its
    self-test fails if a second one appears.

    Never raises into the caller's path: an observability write must not be able to break a
    turn that already went wrong. A marker that cannot be built is itself reported, at
    WARNING, through the same single call.

    That promise used to be FALSE, and adversarial review round 28 proved it with a
    synthetic handler whose ``emit()`` raises: only marker CONSTRUCTION was guarded, while
    the emit itself sat outside the ``try``, so a failing handler escaped into the
    empty-turn finalizer -- the one code path whose entire job is to rescue a turn that
    already went wrong. The delivery is now inside the guard too.

    Which makes this honestly BEST-EFFORT, not guaranteed: when the sink itself is broken
    there is no channel left to report that on, so the failure is invisible by
    construction. Saying so here is the only thing that keeps a missing marker from being
    read as "no empty turn happened".
    """
    try:
        line = build_marker(payload, phase, chat_id, message_id, notice)
        level = logging.INFO
    except Exception as exc:  # noqa: BLE001 -- see docstring
        # `notice=unknown`: the marker could not be built, so this line must not
        # claim the user was told anything. A value outside ALLOWED_NOTICES on
        # purpose -- the reader treats an unknown notice as not-explained, which
        # is the safe direction.
        line = ("{} service=owui phase=marker_unbuildable notice=unknown cause={} "
                "trace=- chat=- msg=- err={}").format(
            MARKER_EMPTY_REPLY, CAUSE_EMPTY_FINALIZED, type(exc).__name__
        )
        level = logging.WARNING
    try:
        _MARKER_LOG.log(level, line)
    except Exception:  # noqa: BLE001 -- a broken log sink must not break a turn
        pass
    return line


def build_banner(cause: str, trace_id: str) -> str:
    """The single source of the visible error banner text (cause + trace id only)."""
    return (
        "This response finished without any content (cause: {}). "
        "Nothing was delivered — retry, or share trace {} with ops."
    ).format(cause, trace_id)

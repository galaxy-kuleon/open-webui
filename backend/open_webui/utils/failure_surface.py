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

# THE OTHER HALF OF THE LIFECYCLE, and the reason it exists.
#
# Measured on the live 8083 database on 2026-08-13: of nine blank assistant turns
# since the ledger began, FIVE carry no cause anywhere -- `done=False`, no error
# on the row, and no ledger record of any kind. Four of those five belong to one
# real user on the direct-model path.
#
# The reason is structural, not a missing field. Every marker this module emits
# is written by the finalizer, so a turn that dies BEFORE the finalizer -- worker
# death, container replacement, a socket that drops before terminalization --
# emits nothing at all. Its absence is then indistinguishable from a turn that
# never happened, and "we have no record" reads as "nothing went wrong".
#
# `turn_opened` is written when the assistant placeholder row is created, which
# is the earliest moment the turn provably exists. An opened turn with no
# terminal record is an UNTERMINATED turn: the population that was previously
# invisible by construction.
MARKER_TURN_OPENED = "turn_opened"

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

# WHY the stream died, decided from the EXCEPTION TYPE rather than from its text.
#
# The ops report classifies truncations by grepping the stored error string, and
# adversarial review round 33 showed both failure directions of that: a genuine
# truncation spelled differently (httpx ``RemoteProtocolError``, aiohttp
# ``ServerDisconnectedError``) stays generic, and ordinary assistant prose quoting
# an error can fabricate one. It said the honest fix is a producer-owned closed
# enum chosen from the caught exception, and this is it.
#
# Keys are exception CLASS NAMES, matched against the raised type and its bases,
# so no message text is ever consulted. Unknown types get ``unclassified`` --
# which is the point: an unfamiliar failure must read as unfamiliar, not as the
# nearest label that happens to match a substring.
# (module prefix, class name) -> label. The MODULE half is not decoration: keying
# on the bare name accepted any foreign class that happened to share it, so an
# unrelated `TransferEncodingError` from some business-rule module classified as
# a transport truncation without anyone reading a message. Round 36 built exactly
# that collision. A prefix rather than an exact module so aiohttp/httpx moving a
# class between submodules does not silently blind this.
FAILURE_KIND_BY_EXCEPTION = {
    # the wire ended mid-body: the user watched an answer stop
    ("aiohttp", "TransferEncodingError"): "stream_truncated",
    ("aiohttp", "ContentLengthError"): "stream_truncated",
    ("aiohttp", "ClientPayloadError"): "stream_truncated",
    ("httpx", "RemoteProtocolError"): "stream_truncated",
    ("http.client", "IncompleteRead"): "stream_truncated",
    ("httpcore", "RemoteProtocolError"): "stream_truncated",
    # one line was too big for the reader; the wire was fine
    ("aiohttp", "LineTooLong"): "sse_line_too_long",
    # The peer went away. HONEST NOTE, measured 2026-08-13 with
    # `scripts/ops/owui_transport_classifier_probe.sh`: a disconnect BEFORE the
    # response headers raises ServerDisconnectedError at the REQUEST, not at the
    # body read, so it never reaches the adapter and is `unclassified` by design
    # -- the read boundary correctly declines to speak for a failure that
    # happened before it. A disconnect mid-body surfaces as ClientPayloadError
    # instead. So `peer_disconnected` may be unreachable through this path; the
    # entries stay because they cost nothing and a future direct client would
    # use them, but nobody should read a zero there as "no peer ever
    # disconnected".
    ("aiohttp", "ServerDisconnectedError"): "peer_disconnected",
    ("aiohttp", "ClientConnectionError"): "peer_disconnected",
    ("builtins", "ConnectionResetError"): "peer_disconnected",
    # it never finished in time
    ("builtins", "TimeoutError"): "upstream_timeout",
    ("asyncio", "TimeoutError"): "upstream_timeout",
    ("aiohttp", "ServerTimeoutError"): "upstream_timeout",
    ("httpx", "ReadTimeout"): "upstream_timeout",
    ("httpcore", "ReadTimeout"): "upstream_timeout",
}
FAILURE_KIND_UNCLASSIFIED = "unclassified"
ALLOWED_FAILURE_KINDS = frozenset(
    set(FAILURE_KIND_BY_EXCEPTION.values()) | {FAILURE_KIND_UNCLASSIFIED})


class StreamReadFailure(Exception):
    """A failure raised while READING the upstream body, already classified.

    Exists so the read boundary can state where the failure happened and the
    handler above can stop guessing. Everything else escaping that handler --
    filters, DB writes, serialization, the notification path -- is deliberately
    NOT one of these and stays `unclassified`.
    """

    def __init__(self, failure_kind: str, cause: BaseException):
        super().__init__(failure_kind)
        self.failure_kind = failure_kind
        self.__cause__ = cause


async def classified_body_reads(body_iterator):
    """Yield from `body_iterator`, tagging read failures with a closed label.

    The whole point is WHERE, not what: a TimeoutError from the model's body and
    a TimeoutError from a UI event emitter are the same class and completely
    different facts. Round 36 caught the second being reported as the first,
    which would send an operator to stare at a provider that had answered fine.

    `StopAsyncIteration` ends the loop normally and is not a failure.
    """
    iterator = body_iterator.__aiter__()
    while True:
        try:
            chunk = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except Exception as exc:  # noqa: BLE001 -- classified and re-raised
            raise StreamReadFailure(classify_exception(exc), exc) from exc
        yield chunk


def classify_exception(exc) -> str:
    """Closed failure label for a caught exception, from its TYPE only.

    Walks the MRO so a subclass of a known error still classifies, and matches on
    (module, qualname) rather than the bare class name: round 36 showed a foreign
    class merely NAMED ``TransferEncodingError`` classified as a real truncation.

    Never reads ``str(exc)``: the message can contain provider text, user content
    echoed back, or a quoted error from a previous turn -- all three are how a
    text-matching classifier gets fabricated input (round 33).
    """
    for klass in type(exc).__mro__:
        module = (getattr(klass, "__module__", "") or "").split(".")
        for depth in range(len(module), 0, -1):
            kind = FAILURE_KIND_BY_EXCEPTION.get(
                (".".join(module[:depth]), klass.__qualname__))
            if kind:
                return kind
    return FAILURE_KIND_UNCLASSIFIED


# WHAT THE FINALIZER ACTUALLY SAW, which is not what this used to say.
#
# This was `db_stream_flush` -- "the answer completed and the write did not". No
# code observed that. The normal finalization path writes done/content/output to
# the database FIRST, discards the return value, and only afterwards notices
# there is no answerable output and asks for a label. If that write had really
# failed, execution would not have reached the label at all.
#
# So every normally-finalized blank -- a provider EOF with nothing in it, a
# reasoning-only completion, anything -- told the user a persistence story, and
# every layer below faithfully preserved it: the banner they read, the frontend,
# the ledger, the ops report. Nothing in the chain could disagree, because
# nothing in the chain had measured it. Partner review round 59 ranked it the
# highest-harm inferred label in the stack for exactly that reason.
#
# The honest label names the observation: the turn finalized and there was no
# answerable output. If a persistence cause is ever wanted, it needs a NEW
# observation -- the upsert return checked at the write site -- not a rename.
CAUSE_EMPTY_FINALIZED = "finalized_no_answer"

# ...and an unknown phase is its own answer. It used to fall back to the
# finalized cause, so "we could not tell which boundary this was" was reported
# as a specific diagnosis of the one boundary it might not have been.
CAUSE_EMPTY_UNKNOWN = "empty_outcome_unknown"

# RETIRED THE SAME DAY IT SHIPPED, and readable forever after.
#
# It was written by a startup sweep that marked unfinished turns as interrupted
# by a restart. Adversarial review returned NO-PASS: nothing in that predicate
# observed a process, an owner or a generation -- only that startup found a row
# unfinished -- so the label asserted a cause it had not measured, which is the
# exact defect `db_stream_flush` was retired for hours earlier.
#
# Three real rows on live 8083 carry it. Their original causes were never
# recorded, so rewriting them back would swap one guess for another; they stay,
# and this stays readable so a reader meeting one knows what it does and does
# not mean. It is deliberately NOT in ALLOWED_CAUSES: nothing may emit it again.
CAUSE_INTERRUPTED_BY_RESTART_RETIRED = "interrupted_by_restart"

#: Emitted by nothing; READ from historical rows. Every blank turn stored before
#: 2026-08-13 carries it, and dropping it from the readable set would silently
#: reclassify real history as unrecognised.
CAUSE_EMPTY_LEGACY_FLUSH = "db_stream_flush"

# The CANONICAL set of cause labels this slice is permitted to emit. It is a subset/alias of
# scripts/ops/openwebui_8083_chat_triage.py ``CAUSES``. It is enforced FAIL-LOUD in
# ``build_error_payload`` / ``assert_privacy_safe`` so an arbitrary or raw string can NEVER
# reach ``error.cause`` or the rendered banner (M4). Single-sourcing against drift is
# additionally enforced at TEST time (``test_failure_surface`` cross-checks this set against
# ``triage.CAUSES`` and fails on divergence) — the owui container cannot import the
# parent-repo ops script, so an import-time cross-check here is not possible.
# ...and the cause for a turn that ended EARLY. Without this the closed grammar
# had exactly one cause, so an interrupted empty turn had no vocabulary to
# describe itself and was told to the user as `db_stream_flush` -- a
# persistence story for what was an interruption. Reproduced live on 8083:
# the producer logged `phase=interrupted failure=unclassified` and the banner
# said "cause: db_stream_flush" in the same breath. The phase was known; the
# banner just could not say it, which sends the reader to the database for
# something the database never touched.
CAUSE_EMPTY_INTERRUPTED = "stream_interrupted"

ALLOWED_CAUSES = frozenset({CAUSE_EMPTY_FINALIZED, CAUSE_EMPTY_INTERRUPTED,
                           CAUSE_EMPTY_UNKNOWN})

#: The cause each phase is allowed to report. Derived, never passed in beside
#: the phase, so the two cannot disagree -- which is exactly how they came to
#: disagree in the first place.
CAUSE_FOR_PHASE = {
    PHASE_FINALIZED: CAUSE_EMPTY_FINALIZED,
    PHASE_INTERRUPTED: CAUSE_EMPTY_INTERRUPTED,
}


def cause_for_phase(phase: str) -> str:
    """The cause label that matches an empty turn's phase.

    An unknown phase does not raise -- a banner is the last thing standing
    between a user and a blank box, and it must not be the thing that fails --
    but it no longer borrows the finalized cause either. "We could not tell
    which boundary this was" is a different fact from "it finalized with no
    answer", and reporting the second for the first is how an unclassified
    turn acquires a specific diagnosis nobody measured.
    """
    return CAUSE_FOR_PHASE.get(phase, CAUSE_EMPTY_UNKNOWN)

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
                 notice: str, failure: str = FAILURE_KIND_UNCLASSIFIED) -> str:
    """The ledger line for an empty turn: closed vocabulary, ids only, no content.

    Separated from ``log_empty_turn`` so a test can assert the exact bytes without a
    logging handler, and so the ledger's parser can be exercised against the real
    producer's output rather than a hand-copied string.
    """
    if phase not in ALLOWED_PHASES:
        raise ValueError("phase {!r} is not canonical; allowed: {}".format(phase, sorted(ALLOWED_PHASES)))
    if notice not in ALLOWED_NOTICES:
        raise ValueError("notice {!r} is not canonical; allowed: {}".format(notice, sorted(ALLOWED_NOTICES)))
    if failure not in ALLOWED_FAILURE_KINDS:
        raise ValueError("failure {!r} is not canonical; allowed: {}".format(
            failure, sorted(ALLOWED_FAILURE_KINDS)))
    assert_privacy_safe(payload)
    # `_KV_RE` in journey_ledger splits on whitespace, so a value containing a space would
    # silently truncate the record. Every value here is an opaque id or a label from a
    # closed set, and this states that rather than assuming it.
    for key, val in (("cause", payload["cause"]), ("trace", payload["trace_id"]),
                     ("chat", chat_id or ""), ("msg", message_id or "")):
        if val != "".join(val.split()):
            raise ValueError("marker field {!r} contains whitespace: {!r}".format(key, val))
    return (
        "{} service=owui phase={} notice={} failure={} cause={} trace={} chat={} msg={}".format(
            MARKER_EMPTY_REPLY, phase, notice, failure, payload["cause"],
            payload["trace_id"], chat_id or "-", message_id or "-",
        )
    )


def log_empty_turn(payload: dict, phase: str, chat_id: str, message_id: str,
                   notice: str, failure: str = FAILURE_KIND_UNCLASSIFIED) -> str:
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
        line = build_marker(payload, phase, chat_id, message_id, notice, failure)
        level = logging.INFO
    except Exception as exc:  # noqa: BLE001 -- see docstring
        # `notice=unknown`: the marker could not be built, so this line must not
        # claim the user was told anything. A value outside ALLOWED_NOTICES on
        # purpose -- the reader treats an unknown notice as not-explained, which
        # is the safe direction.
        line = ("{} service=owui phase=marker_unbuildable notice=unknown "
                "failure=unclassified cause={} trace=- chat=- msg=- err={}").format(
            MARKER_EMPTY_REPLY, CAUSE_EMPTY_FINALIZED, type(exc).__name__
        )
        level = logging.WARNING
    _emit(level, line)
    return line


def _emit(level: int, line: str) -> None:
    """THE single logging call in this module -- see the module docstring.

    Extracted rather than duplicated: the ledger's self-test walks this file's
    AST and requires `_MARKER_LOG` to be touched exactly once, as `.log`. That
    guard is what makes "any line on this logger came from this file" true, so a
    second emitter has to funnel through here rather than sit beside it.
    """
    try:
        _MARKER_LOG.log(level, line)
    except Exception:  # noqa: BLE001 -- a broken log sink must not break a turn
        pass


def build_turn_opened_marker(chat_id: str, message_id: str, model: str) -> str:
    """The ledger line for a turn that has provably begun. Ids and a model only.

    `model` is a configured id from this deployment, never user text; it is
    validated for whitespace like every other field because `_KV_RE` splits on
    it and a space would silently truncate the record.
    """
    for key, val in (("chat", chat_id or ""), ("msg", message_id or ""),
                     ("model", model or "")):
        if val != "".join(val.split()):
            raise ValueError("marker field {!r} contains whitespace: {!r}".format(key, val))
    return "{} service=owui chat={} msg={} model={}".format(
        MARKER_TURN_OPENED, chat_id or "-", message_id or "-", model or "-",
    )


MARKER_TURN_INTERRUPTED = "assistant_turn_interrupted"

#: The closed reason set for an interrupted turn. Same discipline as every other
#: vocabulary here: a value outside it becomes `unknown` rather than reaching the
#: ledger as free text.
INTERRUPTED_REASONS = frozenset({
    "cancelled", "upstream_read_failed", "stream_processing_failed",
    "stream_failed",
})


#: `persisted` is THREE states, not a boolean. `no` must mean "the rescue write
#: was attempted and did not land" -- an operator seeing it should go looking for
#: lost text. A channel turn has no persistence path at all, so reporting `no`
#: there would manufacture a data-loss incident out of a design decision.
PERSISTED_YES = "yes"
PERSISTED_NO = "no"
PERSISTED_NOT_APPLICABLE = "n/a"
ALLOWED_PERSISTED = frozenset({PERSISTED_YES, PERSISTED_NO,
                               PERSISTED_NOT_APPLICABLE})


def build_turn_interrupted_marker(chat_id: str, message_id: str, reason: str,
                                  failure: str, persisted) -> str:
    """The record that finally says WHICH message a truncation interrupted.

    This event already existed and already knew all of this — it was emitted on
    `utils.middleware`, which the ledger trusts COUNT-ONLY because that module
    also logs provider text. So the identity was stripped and `msg` was
    deliberately never included: widening a trust boundary for convenience is
    how the boundary stops meaning anything.

    Moving the emission here resolves that tension instead of working around it.
    This module's whole contract is that nothing free-form reaches its logger,
    which is exactly what makes an identity-bearing record safe. Same event, same
    fields, a producer that is allowed to name the turn.

    `persisted` is the CHECKED upsert result, not the absence of an exception —
    the distinction this stack retired elsewhere the same week.
    """
    for key, val in (("chat", chat_id or ""), ("msg", message_id or "")):
        if val != "".join(val.split()):
            raise ValueError("marker field {!r} contains whitespace: {!r}".format(key, val))
    safe_reason = reason if reason in INTERRUPTED_REASONS else "unknown"
    safe_failure = failure if failure in ALLOWED_FAILURE_KINDS else FAILURE_KIND_UNCLASSIFIED
    if isinstance(persisted, str):
        safe_persisted = persisted if persisted in ALLOWED_PERSISTED else PERSISTED_NOT_APPLICABLE
    else:
        safe_persisted = PERSISTED_YES if persisted else PERSISTED_NO
    return (
        "{} service=owui reason={} failure={} persisted={} chat={} msg={}".format(
            MARKER_TURN_INTERRUPTED, safe_reason, safe_failure, safe_persisted,
            chat_id or "-", message_id or "-",
        )
    )


def log_turn_interrupted(chat_id: str, message_id: str, reason: str,
                         failure: str, persisted: bool) -> str:
    """Emit it, and never let an unbuildable line erase the event.

    Same rule as `log_turn_opened`: a record that cannot be built drops its ids
    rather than vanishing. An interruption nobody recorded reads as a turn that
    was never interrupted, which is the more dangerous silence.
    """
    try:
        line = build_turn_interrupted_marker(chat_id, message_id, reason,
                                             failure, persisted)
    except Exception:  # noqa: BLE001 -- see below
        # NOTHING HERE MAY RAISE INTO THE CALLER. This fires inside
        # `save_interrupted_state`, on a turn that is ALREADY failing and is in
        # the middle of rescuing the user's partial text to disk. An exception
        # escaping an observability helper at that moment would turn a
        # recoverable truncation into a lost answer -- the instrument destroying
        # the thing it exists to measure. The first version caught only
        # ValueError, which covers the whitespace refusal and nothing else.
        try:
            line = build_turn_interrupted_marker("", "", reason, failure,
                                                 persisted)
        except Exception:  # noqa: BLE001
            return ""
    _emit(logging.INFO, line)
    return line


def log_turn_opened(chat_id: str, message_id: str, model: str) -> str:
    """Record that a turn began. Best-effort, and never raises into the caller.

    Same promise as `log_empty_turn` and the same honest limit: when the sink
    itself is broken there is no channel left to say so. The difference is which
    direction the silence points. A missing empty-turn marker hides a failure; a
    missing turn_opened marker hides a turn's EXISTENCE, which would make an
    unterminated turn look like one that never started -- the exact confusion
    this marker was added to end. So an unbuildable line is still emitted, with
    the ids dropped, rather than the turn going unrecorded.
    """
    try:
        line = build_turn_opened_marker(chat_id, message_id, model)
        level = logging.INFO
    except Exception as exc:  # noqa: BLE001 -- see docstring
        line = "{} service=owui chat=- msg=- model=- err={}".format(
            MARKER_TURN_OPENED, type(exc).__name__
        )
        level = logging.WARNING
    _emit(level, line)
    return line


def build_banner(cause: str, trace_id: str) -> str:
    """The single source of the visible error banner text (cause + trace id only)."""
    return (
        # "Nothing was delivered" was never observed. The server knows there
        # is no answerable output stored; it does not know what reached the
        # browser -- reasoning, status and tool markup may already be on screen,
        # and `await sio.emit` is emit acceptance, not a browser acknowledgement.
        # Telling someone nothing arrived when something did sends them to retry
        # work they can already see.
        "This turn ended without a final answer (cause: {}). "
        "Retry, or share trace {} with ops."
    ).format(cause, trace_id)


# THE CLIENT SAVE ROUTE, which is the only way a browser can move `done`.
#
# `POST /api/v1/chats/{id}` replaces the whole chat blob with the client's copy
# and then reconciles the normalized rows from it, so a tab whose mirror predates
# a terminal write re-asserts the pre-terminal state into BOTH stores. Every
# terminal write this module exists to record -- the finalized-empty banner, the
# interrupted-turn rescue -- is undoable by that route, silently, with no record
# that anything was undone.
#
# ONE kind for BOTH outcomes, and the accept line is the point of it. A guard
# that fires zero times must be distinguishable from a guard that was never
# wired: at eleven full-blob saves per forty-eight hours on live 8083, a
# rejection-only marker looks identical either way, and this stack has already
# shipped one P0 fix that never once ran while its silence read as health.
# `outcome=` carries the distinction, so a zero always arrives with its own
# positive control beside it.
MARKER_CLIENT_SAVE = "client_save_checked"

#: What the guard did with this request. `accepted` = no conflict was found and
#: the save was applied. `rejected` = a conflict was found and NOTHING was
#: written to either store. `would_reject` = a conflict was found and the save
#: was applied anyway, because the deployment is running the guard in observe
#: mode; it is the staged-rollout state and it is NOT a safe steady state.
CLIENT_SAVE_ACCEPTED = "accepted"
CLIENT_SAVE_REJECTED = "rejected"
CLIENT_SAVE_WOULD_REJECT = "would_reject"
ALLOWED_CLIENT_SAVE_OUTCOMES = frozenset({
    CLIENT_SAVE_ACCEPTED, CLIENT_SAVE_REJECTED, CLIENT_SAVE_WOULD_REJECT,
})

#: WHY it was refused, from a closed set. `none` accompanies `accepted` and is
#: spelled rather than omitted: a missing key and a key meaning "nothing was
#: wrong" are different records, and the ledger's `_KV_RE` cannot tell an absent
#: field from one nobody wrote.
CLIENT_SAVE_REASON_NONE = "none"
CLIENT_SAVE_REASON_STALE_TURN_STATE = "stale_turn_state"
ALLOWED_CLIENT_SAVE_REASONS = frozenset({
    CLIENT_SAVE_REASON_NONE, CLIENT_SAVE_REASON_STALE_TURN_STATE,
})


def build_client_save_marker(chat_id: str, message_id: str, outcome: str,
                             reason: str) -> str:
    """The ledger line for one guarded client save. Ids and closed labels only.

    Deliberately carries NO field values and no counts of anything the client
    sent: the conflicting field's stored and claimed values are message content
    in every practically interesting case, and this logger's whole trust rests
    on nothing free-form reaching it. An operator who needs the detail has the
    chat and message id and can read the row.
    """
    if outcome not in ALLOWED_CLIENT_SAVE_OUTCOMES:
        raise ValueError("outcome {!r} is not canonical; allowed: {}".format(
            outcome, sorted(ALLOWED_CLIENT_SAVE_OUTCOMES)))
    if reason not in ALLOWED_CLIENT_SAVE_REASONS:
        raise ValueError("reason {!r} is not canonical; allowed: {}".format(
            reason, sorted(ALLOWED_CLIENT_SAVE_REASONS)))
    # Same whitespace refusal as every other marker here: `_KV_RE` splits on
    # whitespace, so a value containing a space silently truncates the record.
    for key, val in (("chat", chat_id or ""), ("msg", message_id or "")):
        if val != "".join(val.split()):
            raise ValueError("marker field {!r} contains whitespace: {!r}".format(key, val))
    return (
        "{} service=owui outcome={} reason={} chat={} msg={}".format(
            MARKER_CLIENT_SAVE, outcome, reason, chat_id or "-", message_id or "-",
        )
    )


def log_client_save(chat_id: str, message_id: str, outcome: str,
                    reason: str) -> str:
    """Emit it, and never raise into the request.

    Same promise as the other emitters here, and the same honest limit: when the
    sink itself is broken there is no channel left to say so. The direction of
    the silence is what decides the fallback -- a lost ACCEPT line only shrinks a
    denominator, but a lost REJECT line turns a refused save into a save that
    looks like it never happened, so an unbuildable line is still emitted with
    its ids dropped rather than the event going unrecorded.
    """
    try:
        line = build_client_save_marker(chat_id, message_id, outcome, reason)
        level = logging.INFO
    except Exception as exc:  # noqa: BLE001 -- see docstring
        # `outcome=marker_unbuildable`: a value deliberately OUTSIDE
        # ALLOWED_CLIENT_SAVE_OUTCOMES, exactly as `log_empty_turn` does with
        # `phase=`. Naming either real outcome here would INVENT one -- claiming
        # a rejection that may not have happened, or an acceptance that may not
        # have either -- and the reader treats an unknown outcome as
        # not-measured, which is the safe direction.
        line = ("{} service=owui outcome=marker_unbuildable reason={} "
                "chat=- msg=- err={}").format(
            MARKER_CLIENT_SAVE, CLIENT_SAVE_REASON_NONE, type(exc).__name__,
        )
        level = logging.WARNING
    _emit(level, line)
    return line

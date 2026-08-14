"""Accepted-execution conservation for OpenWebUI chat turns. Schema 1.

WHAT THIS MEASURES, EXACTLY: that an execution attempt was accepted, and how the
owner of that execution finally disposed of itself. Nothing else.

WHAT IT DOES NOT MEASURE, and may never be read as: whether the user got an
answer, whether anything was saved, whether the browser received a byte, which
provider or route ran, or whether the turn was "complete". `returned` means the
Python owner returned normally. `process_chat` can catch an exception, notify
and persist locally, and return `None` — that is `returned` too.

WHY "EXECUTION" AND NOT "TASK". The unit deliberately spans two owners: an
asyncio Task, and the inline `await` that has no Task at all. `not_started`
belongs to neither an accepted Task nor the inline path — it means no owner was
ever constructed. Calling all of that a "task lifecycle" would have made the
name quietly false for a whole path, so `execution_mode` says which owner ran
and the outcome vocabulary stays single.

WHY IT IS SEPARATE FROM `turn_opened`. That marker observes a *persisted
assistant placeholder*. This one observes an *accepted execution*. Neither is a
proxy for the other: a placeholder can be written and never execute, and a
local/channel/API attempt can execute with no placeholder at all.

WHO OWNS THE TERMINAL. For a Task, the done callback and ONLY the done callback.
A coroutine-level `finally` looks like it knows the answer and does not: a
`finally` doing cleanup can still replace a chosen return with a BaseException,
so a terminal emitted inside the coroutine can permanently win before the Task
becomes `raised`. The done callback is the first point at which the disposition
is immutable.

WHY THE ATTEMPT ID IS OPAQUE. `secrets.token_hex(16)`, bound to nothing — not
the chat, the message, the user, or the model. Schema 1's only claim is
conservation arithmetic; an id that could be joined to a person would invite
exactly the inferences this schema is too small to support.

NEVER ADD A FIELD TO SCHEMA 1. Its small meaning is the stable denominator.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading

#: The dedicated logger. Its whole value is that ANY line on it came from this
#: module, so the collector can allow-list it wholesale instead of pattern-
#: matching prose. Every emission funnels through one call below.
_LIFECYCLE_LOG = logging.getLogger("open_webui.turn_lifecycle")

#: Bumped only for an incompatible grammar change. A reader meeting an unknown
#: schema must count the row as physically present and semantically unusable,
#: never silently parse it as schema 1.
SCHEMA = 1

MARKER_TURN_STARTED = "turn_started"
MARKER_TURN_TERMINAL = "turn_terminal"

#: WHERE the attempt was accepted. Not a route and not a provider.
SCOPE_STORED_CHAT = "stored_chat"
SCOPE_CHANNEL = "channel"
SCOPE_LOCAL = "local"
SCOPE_API = "api"
ALLOWED_SCOPES = frozenset({SCOPE_STORED_CHAT, SCOPE_CHANNEL, SCOPE_LOCAL,
                            SCOPE_API})

#: WHICH OWNER ran the execution. `task` is an asyncio Task whose done callback
#: owns the terminal; `inline` is a direct await with no Task and no callback.
MODE_TASK = "task"
MODE_INLINE = "inline"
ALLOWED_MODES = frozenset({MODE_TASK, MODE_INLINE})

#: HOW THE OWNER FINALLY DISPOSED OF ITSELF. Exhaustive and disjoint because
#: they are read from the FINAL state, never from race order:
#:
#:     if task.cancelled():        cancelled
#:     elif task.exception() is None: returned
#:     else:                       raised
#:
#: A cancellation that the coroutine suppressed is `returned` — `cancel()` is
#: intent, not disposition. A cancellation whose cleanup raises is `raised`. A
#: non-CancelledError BaseException is `raised`, so this must never be narrowed
#: to `Exception`.
#:
#: `not_started` means NO OWNER WAS EVER CONSTRUCTED. Once `create_task`
#: returns, it is forever forbidden — a Redis or bookkeeping failure afterwards
#: does not un-create the task.
#:
#: `unknown` is a first-class answer, not a failure of nerve: it is the only
#: honest value when an owner is known to have existed but cannot be inspected.
#: Coercing it to `returned` would move an unobserved attempt into the healthy
#: count, which is the move that made three earlier labels lie.
OUTCOME_RETURNED = "returned"
OUTCOME_RAISED = "raised"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_NOT_STARTED = "not_started"
OUTCOME_UNKNOWN = "unknown"
ALLOWED_OUTCOMES = frozenset({OUTCOME_RETURNED, OUTCOME_RAISED,
                              OUTCOME_CANCELLED, OUTCOME_NOT_STARTED,
                              OUTCOME_UNKNOWN})

#: Exactly what `secrets.token_hex(16)` produces. Anchored, lower-case only.
_ATTEMPT_RE = re.compile(r"\A[0-9a-f]{32}\Z")

#: 128 bits, so collision across a process lifetime needs no mitigation.
_ATTEMPT_ENTROPY_BYTES = 16

#: Live attempts, by id. Insertion happens BEFORE the owner is constructed, so a
#: fast owner's callback can never fire against a lifecycle that does not exist
#: yet — that ordering is what keeps `orphan terminal` exceptional rather than
#: structural. Entries are removed by `finish` and by nothing else: a Redis
#: failure must never pop a lifecycle whose task is still running.
_REGISTRY: "dict[str, Attempt]" = {}
_REGISTRY_LOCK = threading.Lock()


def new_attempt_id() -> str:
    return secrets.token_hex(_ATTEMPT_ENTROPY_BYTES)


def _validate(attempt: str, scope: str, mode: str) -> None:
    """FAIL LOUD on a malformed record rather than emit an unparseable one.

    A record the collector cannot parse is worse than no record: it lands in the
    physical count, misses the semantic one, and looks like data loss.
    """
    if not _ATTEMPT_RE.match(attempt or ""):
        raise ValueError("attempt id must be 32 lower-case hex characters")
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"scope must be one of {sorted(ALLOWED_SCOPES)}")
    if mode not in ALLOWED_MODES:
        raise ValueError(f"execution_mode must be one of {sorted(ALLOWED_MODES)}")


def build_turn_started(attempt: str, scope: str, mode: str) -> str:
    _validate(attempt, scope, mode)
    return (f"{MARKER_TURN_STARTED} service=owui schema={SCHEMA} "
            f"attempt={attempt} scope={scope} execution_mode={mode}")


def build_turn_terminal(attempt: str, scope: str, mode: str,
                        outcome: str) -> str:
    _validate(attempt, scope, mode)
    if outcome not in ALLOWED_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(ALLOWED_OUTCOMES)}")
    return (f"{MARKER_TURN_TERMINAL} service=owui schema={SCHEMA} "
            f"attempt={attempt} scope={scope} execution_mode={mode} "
            f"execution_outcome={outcome}")


def _emit(line: str) -> None:
    """THE single logging call in this module.

    Guarded because a broken log sink must never break a turn — and because a
    lost record is honest here: it becomes a start with no terminal, which the
    reader renders as unmeasured rather than as failure. It must NOT retry: a
    retry after a raising handler is how one attempt gets two terminals.
    """
    try:
        _LIFECYCLE_LOG.info(line)
    except Exception:  # noqa: BLE001 -- observability must not break the observed
        pass


class Attempt:
    """One accepted execution, and the sole owner of its terminal.

    The three flags are independent and monotonic, and none of them is a
    delivery acknowledgement — the logger offers no ACK, so `start_attempted`
    means "the start line was handed to the sink", never "a start row exists".
    That is precisely why the reader must keep `orphan terminal` as a real
    outcome instead of proving it impossible.

    `finish` is synchronous and never awaits, so two callers cannot interleave
    across an await; and it claims the terminal BEFORE the side effect, so a
    raising or re-entrant handler cannot produce a second one.
    """

    __slots__ = ("id", "scope", "mode", "start_attempted", "terminal_claimed",
                 "terminal_attempted", "_lock")

    def __init__(self, attempt_id: str, scope: str, mode: str) -> None:
        self.id = attempt_id
        self.scope = scope
        self.mode = mode
        self.start_attempted = False
        self.terminal_claimed = False
        self.terminal_attempted = False
        self._lock = threading.Lock()

    @property
    def finished(self) -> bool:
        return self.terminal_claimed

    def finish(self, outcome: str) -> bool:
        """Emit this attempt's one terminal. True if THIS call emitted it.

        A second call is not an error: several owners legitimately race (a done
        callback, a shutdown takeover), and exactly-once means they may all try.
        The return value tells the losers to stay silent rather than guess.
        """
        with self._lock:
            if self.terminal_claimed:
                return False
            self.terminal_claimed = True
        try:
            line = build_turn_terminal(self.id, self.scope, self.mode, outcome)
        except ValueError:
            # An invalid outcome is a programming error. It must not break the
            # turn, and it must not silently become a healthy label either. The
            # attempt stays claimed and nothing is written, which the reader
            # counts as a missing terminal.
            self._unregister()
            return True
        self.terminal_attempted = True
        _emit(line)
        self._unregister()
        return True

    def _unregister(self) -> None:
        """Remove ONLY this lifecycle, never whatever now holds the id."""
        with _REGISTRY_LOCK:
            if _REGISTRY.get(self.id) is self:
                del _REGISTRY[self.id]


def start(scope: str, mode: str) -> "Attempt | None":
    """Open one accepted execution. `None` if the grammar is invalid.

    ORDER IS THE POINT. The lifecycle is registered before the start line is
    attempted, and both happen before the caller constructs an owner — so a fast
    owner's done callback can never fire against a lifecycle that does not exist.
    Reversing this lets a quick task terminalize while no start exists, which
    turns `orphan terminal` from an instrumentation fault into normal traffic.

    Returning `None` rather than raising keeps the caller's worst case at "this
    attempt is not measured", never "this turn broke because of observability".
    """
    lifecycle = None
    try:
        # THE WHOLE OPENING IS INSIDE THE GUARD, not just the builder. The first
        # version put `new_attempt_id()` outside it, so an ordinary entropy
        # failure (`OSError` from the RNG) escaped into the request path: a
        # producer of observability could abort a real user's turn. That is the
        # one thing this module must never do.
        attempt_id = new_attempt_id()
        line = build_turn_started(attempt_id, scope, mode)
        lifecycle = Attempt(attempt_id, scope, mode)
        with _REGISTRY_LOCK:
            _REGISTRY[attempt_id] = lifecycle
        lifecycle.start_attempted = True
        _emit(line)
        return lifecycle
    except Exception:  # noqa: BLE001 -- the caller runs unmeasured, never broken
        if lifecycle is not None:
            lifecycle._unregister()
        # NOT `unknown`: no lifecycle was successfully opened, so there is no
        # attempt to report an outcome for. An unopened attempt is absent from
        # the denominator, which is the honest place for it.
        return None
    except BaseException:
        # Process control (KeyboardInterrupt, SystemExit, CancelledError) still
        # propagates -- swallowing those is its own hazard, and this module has
        # no business deciding a shutdown does not apply to it. The registry
        # entry is still removed so a killed start cannot leak a live attempt.
        if lifecycle is not None:
            lifecycle._unregister()
        raise


def classify_task(task) -> str:
    """FINAL disposition of a completed Task. Call only from its done callback.

    Reading `task.exception()` before `done()` raises, and reading it during the
    coroutine's own `finally` reads a state that cleanup can still overturn.
    An inspection failure is `unknown` — never `raised`, which would invent a
    provider-shaped story out of an instrumentation problem.
    """
    try:
        if task.cancelled():
            return OUTCOME_CANCELLED
        return OUTCOME_RETURNED if task.exception() is None else OUTCOME_RAISED
    except BaseException:  # noqa: BLE001 -- CancelledError included, by design
        try:
            if task.cancelled():
                return OUTCOME_CANCELLED
        except BaseException:  # noqa: BLE001
            pass
        return OUTCOME_UNKNOWN


def live_attempts() -> int:
    """Diagnostic only. Never a measurement: it counts this process."""
    with _REGISTRY_LOCK:
        return len(_REGISTRY)


def capability() -> dict:
    """What `/health` advertises.

    PRESENCE IS CAPABILITY, NOT ACTIVITY. It proves this build supports schema
    1; it proves nothing about whether a start or terminal was emitted,
    collected, or survived. The reader needs it to tell "no rows because nothing
    happened" from "no rows because this build predates the producer".
    """
    return {"owui_turn_lifecycle": SCHEMA}

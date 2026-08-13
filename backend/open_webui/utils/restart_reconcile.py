"""Terminalize the turns the PREVIOUS process left unfinished.

WHY THIS EXISTS, measured rather than reasoned about.

On 2026-08-13 three assistant turns on live 8083 were stored `done=False`, empty,
with no error and no ledger record of any kind. Two of them landed 59s and 1m34s
after the owui container was recreated -- under a real user who was
mid-conversation. Across six days the collector recorded 46 owui container
replacements, and on the direct-model path there is no gateway in the request, so
nothing emits `stream_closed_at_shutdown` and nobody ever tells the person a
deploy took their answer.

Every other instrument in this stack is written by the finalizer. A turn whose
process dies never reaches one, so the row stays `done=False` forever and the
browser renders an empty bubble with no spinner and no notice. The turn is not
merely unexplained: it is indistinguishable from one that never happened.

A shutdown hook cannot fix that, because the interesting deaths are the ones that
skip it -- SIGKILL, an OOM, a container replaced out from under the process. So
this runs at STARTUP and reasons backwards: the previous process is gone, and
anything it left unfinished is finished now, whatever ended it.

WHAT IT REFUSES TO DO
  * It will not run when it cannot prove it is the only instance. With Redis
    configured, another worker may be mid-answer on a turn this one can see, and
    marking that turn interrupted would destroy a live answer. Not knowing is
    not permission.
  * It never touches `currentId`. `upsert_message_to_chat_by_id_and_message_id`
    moves it to whatever message it wrote, so sweeping with that helper would
    reorder what a user sees when they open an old chat -- a visible regression
    in exchange for an invisible fix.
  * It never touches a turn that already carries an error, content, or `done`.
    Those have an outcome; this is only for the ones with none.
  * It is bounded in time. Rewriting a turn from three weeks ago tells a user
    nothing they can act on and rewrites history they have already read past.
"""

from __future__ import annotations

import logging

from open_webui.utils import failure_surface

log = logging.getLogger(__name__)

#: How far back a turn may be terminalized. Older than this and the user has
#: long since moved on; the row is history, not a pending answer.
RECONCILE_LOOKBACK_SECONDS = 24 * 60 * 60

#: Never rewrite more than this many turns in one startup. A sweep that suddenly
#: wants thousands is a bug in the predicate, not a very bad night, and the cap
#: turns that into a bounded mistake plus a loud number.
RECONCILE_MAX_TURNS = 200


def should_terminalize(message: dict, now: float,
                       lookback: float = RECONCILE_LOOKBACK_SECONDS) -> bool:
    """Pure: is this a turn the previous process abandoned with no outcome?

    Every clause is a refusal to guess. Content means the user got something;
    `done` means someone terminalized it; an error means it already explains
    itself. What is left is the shape that has no outcome at all.
    """
    if (message or {}).get("role") != "assistant":
        return False
    if (message.get("content") or "").strip():
        return False
    if message.get("done"):
        return False
    if message.get("error"):
        return False
    timestamp = message.get("timestamp")
    if not isinstance(timestamp, (int, float)):
        # No timestamp means we cannot honour the lookback, and a sweep that
        # cannot bound itself must not run on the row.
        return False
    return 0 <= (now - float(timestamp)) <= lookback


def build_terminal_message(message: dict, chat_id: str, message_id: str) -> dict:
    """The fields to write. Same payload builder the live finalizer uses.

    Going through `build_error_payload` rather than composing a banner here is
    what keeps the M4 boundary and the closed cause vocabulary in ONE place: a
    second banner builder is a second thing to keep in sync, and the first
    divergence would be invisible.
    """
    payload = failure_surface.build_error_payload(
        chat_id, message_id, cause=failure_surface.CAUSE_INTERRUPTED_BY_RESTART
    )
    return {**message, "done": True, "error": payload}


def plan_reconciliation(chats: "list", now: float,
                        lookback: float = RECONCILE_LOOKBACK_SECONDS,
                        limit: int = RECONCILE_MAX_TURNS) -> "list[tuple]":
    """Decide, without writing. Returns `[(chat_id, message_id, message), ...]`.

    Separated from the write so the decision can be tested against real chat
    shapes with no database, and so the cap is applied to a decision rather than
    discovered halfway through a mutation.
    """
    planned = []
    for chat in chats:
        chat_id = getattr(chat, "id", None)
        body = getattr(chat, "chat", None)
        if not chat_id or not isinstance(body, dict):
            continue
        messages = ((body.get("history") or {}).get("messages") or {})
        if not isinstance(messages, dict):
            continue
        for message_id, message in messages.items():
            if not isinstance(message, dict):
                continue
            if should_terminalize(message, now, lookback):
                planned.append((chat_id, message_id, message))
                if len(planned) >= limit:
                    return planned
    return planned


def sole_instance(redis_configured: bool) -> bool:
    """May this process speak for every turn it can see?

    Only when nothing else can be serving one. This is the whole safety
    argument, so it is a named function rather than an `if` buried in the sweep:
    when it returns False the sweep does not run, and the reason is logged
    instead of the work being done.
    """
    return not redis_configured

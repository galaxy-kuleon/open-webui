"""COUNT the turns the previous process left unfinished. Do not rewrite them.

WHAT THIS MEASURES, and why it no longer mutates.

On 2026-08-13 three assistant turns on live 8083 were stored `done=False`, empty,
with no error and no ledger record of any kind. Two landed 59s and 1m34s after the
owui container was recreated under a real user who was mid-conversation, and the
collector had recorded 46 owui container replacements in six days. Every
instrument in this stack is written by the finalizer, so a turn whose process
dies never reaches one: the row stays `done=False` forever and the browser
renders an empty bubble with no spinner and no notice.

I first shipped a version of this that FIXED those rows at startup -- marked them
done with a cause and a banner. Adversarial review (round 62) returned NO-PASS
and it was right on every count:

  * `redis is None` is not an ownership boundary. `UVICORN_WORKERS` is
    configurable (`env.py`, `start.sh`); with two workers and no Redis both
    lifespans conclude they are alone, and a second container on the same volume
    is invisible to the test entirely. A race probe against the real function
    showed a concurrently arriving answer being erased: read whole chat, mutate
    stale copy, replace whole chat.
  * The write bypassed the normalized `chat_message` store that live consumers
    prefer, so the two durable stores disagreed about the same turn.
  * `Chats.update_chat_by_id` sets `updated_at=now`, and chat lists sort on it --
    so every swept chat jumped to the top of its owner's list. I checked
    `currentId` and never thought about ordering.
  * The predicate accepts turns carrying tool/code `output`, which this codebase
    explicitly treats as answerable, and overlaid "no final answer" on them.
  * And the label itself was inferred: nothing here observes a process, an owner
    or a generation. What is observed is only "startup found this unfinished".

So this counts, and says what it counts. Fixing the rows needs the shared
`chat+msg` lifecycle with a durable owner generation -- reconcile only an
unmatched opening whose generation is provably retired, re-check at commit time,
write both stores in one transaction, and preserve `updated_at`. Until then a
guess at a destructive boundary is worse than the honest gap it replaces.

The three rows changed by the earlier version are left as they are. Their
original causes were never recorded, so rewriting them back would replace one
guess with another.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: How far back a turn is considered recent enough to be worth reporting. Older
#: than this and the user has long since moved on; the row is history.
RECONCILE_LOOKBACK_SECONDS = 24 * 60 * 60

#: Stop counting past this many. A number this large is a bug in the predicate,
#: not a very bad night, and the cap turns that into a bounded report plus a
#: loud figure rather than an unbounded scan.
RECONCILE_MAX_TURNS = 200


def looks_unfinished(message: dict, now: float,
                     lookback: float = RECONCILE_LOOKBACK_SECONDS) -> bool:
    """Pure: does this row look like a turn nothing ever terminalized?

    NAMED FOR WHAT IT SEES. It was `should_terminalize`, which asserted an
    action; the rename is the honest half of the revert. Every clause is a
    refusal to guess: content means the user got something, `done` means someone
    terminalized it, an error means it already explains itself.

    It is still only a LOOK. It does not observe a process, an owner, an active
    task, the current branch, or `output` -- and a turn carrying tool/code output
    is answerable in this codebase while matching every clause below. That is
    precisely why the caller may report this number and must not act on it.
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
        return False
    return 0 <= (now - float(timestamp)) <= lookback


def count_unfinished(chats: "list", now: float,
                     lookback: float = RECONCILE_LOOKBACK_SECONDS,
                     limit: int = RECONCILE_MAX_TURNS) -> "tuple[int, int, bool]":
    """Count unfinished-looking turns. Returns `(turns, chats, capped)`.

    `capped` is not decoration: a truncated count that does not say it was
    truncated reads exactly like a complete one, and the operator has no way to
    tell a quiet night from a scan that stopped early.
    """
    turns = 0
    chat_ids = set()
    for chat in chats:
        chat_id = getattr(chat, "id", None)
        body = getattr(chat, "chat", None)
        if not chat_id or not isinstance(body, dict):
            continue
        messages = ((body.get("history") or {}).get("messages") or {})
        if not isinstance(messages, dict):
            continue
        for message in messages.values():
            if not isinstance(message, dict):
                continue
            if looks_unfinished(message, now, lookback):
                turns += 1
                chat_ids.add(chat_id)
                if turns >= limit:
                    return turns, len(chat_ids), True
    return turns, len(chat_ids), False

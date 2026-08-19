"""Behavioural contract for the guard on the browser's full-blob chat save.

THE DEFECT. `POST /api/v1/chats/{id}` is the only client-reachable full-history
writer in the product. It one-level-merges the client's payload over the stored
chat, writes the WHOLE blob column, and then reconciles the normalized
`chat_message` rows from that same payload -- which sets `done` and `error` from
client data on both stores and deletes any row the payload omits. So a tab whose
mirror predates a terminal write re-asserts the pre-terminal state, silently,
with no record that anything was undone. Every repair that ends a turn --
`nonstreaming-finalize-147`, `save_interrupted_state`, the startup reconciler --
is undoable through that one route, which makes this guard their precondition
rather than an improvement on them.

WHY THE OBVIOUS GUARD IS THE WRONG ONE, and why these tests are shaped the way
they are. "Never move `done` true->false for an assistant message" is broken by
a live, permitted flow: continue-generation re-opens an EXISTING assistant
message with `done: False`, and it is the SERVER that writes it, through
`Chats.upsert_message_to_chat_by_id_and_message_id` ->
`ChatMessages.upsert_message`. A monotonic rule in that shared path breaks the
server rather than the browser, and fails silently -- leaving a row claiming
`done=true` while a generation is actively running. So the guard sits at the
route, not in the write path, and `test_the_servers_own_placeholder_write_is_...`
drives the real shared path over a real database to prove it is still open.

The tests drive the REAL route handler. Only `Chats` (the store), the failure
marker sink (a real logging handler on the real logger) and the guard's mode
flag are controlled. No assertion reads source text.
"""

import asyncio
import importlib.util
import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models import chats as chats_models
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import ChatForm
from open_webui.routers import chats as chats_router
from open_webui.utils import failure_surface

CHAT_ID = "chat-12345678"
USER_ID = "user-12345678"
USER_MSG = "u-12345678"
ASSISTANT_MSG = "a-12345678"

# THE VOCABULARY, WRITTEN OUT HERE ON PURPOSE, twice over.
#
# First: nothing in this file may import a name the change introduces, or the
# pre-change run dies at COLLECTION and reports an ImportError instead of a
# failed assertion. An import error is not evidence that a behaviour is missing
# -- it is evidence that a file is missing -- and a red made of them proves
# nothing about the guard.
#
# Second: these ARE the contract. The ledger's parser, the report's closed sets
# and any future grep are all written against these exact bytes, so pinning them
# to literals here is what makes a rename in `failure_surface` a red test rather
# than a silently dropped signal.
MARKER_KIND = "client_save_checked"
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_WOULD_REJECT = "would_reject"
REASON_NONE = "none"
REASON_STALE = "stale_turn_state"


def _production(name):
    """A name the change is supposed to add. Missing -> a failed assertion."""
    for module in (failure_surface, chats_models):
        if hasattr(module, name):
            return getattr(module, name)
    raise AssertionError(
        f"{name!r} does not exist: the client-save turn guard is not present "
        f"in this build"
    )


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


class _StoredChat:
    """The shape `ChatResponse(**chat.model_dump())` needs, and nothing else."""

    def __init__(self, chat):
        self.chat = chat

    def model_dump(self):
        return {
            "id": CHAT_ID,
            "user_id": USER_ID,
            "title": self.chat.get("title", "New Chat"),
            "chat": self.chat,
            "updated_at": 1_700_000_000,
            "created_at": 1_700_000_000,
            "archived": False,
        }


class _Chats:
    """Records what reached each store. Both writes are recorded separately
    because the two stores diverging is the observed defect, not a hypothesis.
    """

    def __init__(self, stored):
        self.stored = stored
        self.blob_writes = []
        self.reconciles = []

    async def get_chat_by_id_and_user_id(self, id, user_id, db=None):
        return _StoredChat(self.stored)

    async def update_chat_by_id(self, id, chat, db=None):
        self.blob_writes.append(chat)
        return _StoredChat(chat)

    async def reconcile_messages_by_chat_id(self, chat_id, user_id, messages):
        self.reconciles.append(messages)


class _MarkerSink(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _fields(line):
    """The ledger's own read of a marker line: split on whitespace, then on `=`.

    Deliberately the same crude parse `journey_ledger._KV_RE` performs, so a
    value that would truncate the real record truncates here too.
    """
    out = {"kind": line.split(" ", 1)[0]}
    for token in line.split()[1:]:
        if "=" in token:
            key, val = token.split("=", 1)
            out[key] = val
    return out


async def _drive_async(stored, payload, *, observe=False):
    chats = _Chats(stored)
    sink = _MarkerSink()
    # The marker logger sits at NOTSET, so under pytest's default root level an
    # INFO line is dropped before any handler sees it. Raising it here is a
    # HARNESS fact, not a production one -- in the container GLOBAL_LOG_LEVEL is
    # INFO and `log.debug` on this route is a dead channel, which is exactly why
    # the marker goes through `failure_surface` instead.
    logger = logging.getLogger("open_webui.utils.failure_surface")
    logger.addHandler(sink)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    status = None
    detail = None
    result = None
    try:
        with (
            patch.object(chats_router, "Chats", chats),
            # `create=True`: before the change the flag does not exist, and a
            # patch that refuses to create it would abort the pre-change run at
            # setup instead of letting the behavioural assertion speak.
            patch.object(chats_router, "CLIENT_SAVE_TURN_GUARD_OBSERVE_ONLY",
                         observe, create=True),
        ):
            try:
                result = await chats_router.update_chat_by_id(
                    CHAT_ID,
                    ChatForm(chat=payload),
                    user=SimpleNamespace(id=USER_ID),
                    db=None,
                )
            except HTTPException as exc:
                status = exc.status_code
                detail = exc.detail
    finally:
        logger.removeHandler(sink)
        logger.setLevel(previous_level)
    return SimpleNamespace(result=result, status=status, detail=detail,
                           chats=chats, markers=[_fields(l) for l in sink.lines],
                           raw=sink.lines)


def _drive(*args, **kwargs):
    return asyncio.run(_drive_async(*args, **kwargs))


def _terminal_error():
    return failure_surface.build_error_payload(
        CHAT_ID, ASSISTANT_MSG,
        failure_surface.cause_for_phase(failure_surface.PHASE_FINALIZED),
    )


def _stored_chat(assistant=None, title="a stored title"):
    """A chat as the server holds it: one question, one finished answer."""
    answer = {
        "id": ASSISTANT_MSG,
        "parentId": USER_MSG,
        "childrenIds": [],
        "role": "assistant",
        "content": "the stored answer",
        "done": True,
        "model": "synthetic-model",
    }
    if assistant is not None:
        answer = {**answer, **assistant}
        for key, value in list(assistant.items()):
            if value is _ABSENT:
                del answer[key]
    return {
        "id": CHAT_ID,
        "title": title,
        "models": ["synthetic-model"],
        "history": {
            "currentId": ASSISTANT_MSG,
            "messages": {
                USER_MSG: {
                    "id": USER_MSG,
                    "parentId": None,
                    "childrenIds": [ASSISTANT_MSG],
                    "role": "user",
                    "content": "the stored question",
                },
                ASSISTANT_MSG: answer,
            },
        },
    }


class _Absent:
    def __repr__(self):
        return "<absent>"


_ABSENT = _Absent()


def _client_payload(stored, assistant=None, *, drop=(), extra_messages=None,
                    current_id=None, **chat_fields):
    """What the browser sends: the WHOLE history, plus chat-level fields.

    Built from the stored chat on purpose. A browser's mirror IS the server's
    state plus whatever that tab has since observed, so a payload built any
    other way tests a client that does not exist.
    """
    messages = {
        mid: dict(msg) for mid, msg in
        stored["history"]["messages"].items() if mid not in drop
    }
    if assistant is not None and ASSISTANT_MSG in messages:
        for key, value in assistant.items():
            if value is _ABSENT:
                messages[ASSISTANT_MSG].pop(key, None)
            else:
                messages[ASSISTANT_MSG][key] = value
    for mid, msg in (extra_messages or {}).items():
        messages[mid] = msg
    payload = {
        "models": ["synthetic-model"],
        "history": {
            "currentId": current_id or stored["history"]["currentId"],
            "messages": messages,
        },
        "messages": [],
        "params": {},
        "files": [],
    }
    payload.update(chat_fields)
    return payload


def _only_marker(driven):
    assert len(driven.markers) == 1, driven.raw
    return driven.markers[0]


def _wrote_nothing(driven):
    return not driven.chats.blob_writes and not driven.chats.reconciles


# --------------------------------------------------------------------------
# 1. the undo this guard exists to refuse
# --------------------------------------------------------------------------


def test_a_stale_tab_cannot_undo_a_terminal_write():
    """The whole point. A terminal write landed; a tab that never saw it saves.

    This is the shape that makes every finalizer in this stack non-durable:
    `nonstreaming-finalize-147` writes `done=True` plus the closed
    `finalized_no_answer` payload, and one full-blob save from a tab loaded
    before that write puts `done=False` back into BOTH stores.
    """
    stored = _stored_chat({"done": True, "error": _terminal_error(),
                           "content": ""})
    payload = _client_payload(stored, {"done": False, "error": _ABSENT})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert _wrote_nothing(driven), (
        "a refused save still reached a store: the two writes must be refused "
        "together or the stores diverge, which is the defect this route "
        "already has"
    )
    marker = _only_marker(driven)
    assert marker["kind"] == MARKER_KIND
    assert marker["outcome"] == OUTCOME_REJECTED
    assert marker["reason"] == REASON_STALE
    assert marker["chat"] == CHAT_ID
    assert marker["msg"] == ASSISTANT_MSG


def test_the_refusal_names_the_messages_it_refused():
    """A 409 with no subject is a dead end for whoever has to fix the tab."""
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, {"done": False})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert driven.detail["message_ids"] == [ASSISTANT_MSG]
    assert driven.detail["reason"] == (
        REASON_STALE)


def test_a_stale_tab_cannot_drop_the_stored_banner():
    """The other half of the same undo, and the easier one to miss.

    Keeping `done=True` while dropping `error` leaves the row terminal and the
    user's explanation gone -- a finished-looking turn with no answer and no
    reason, which is strictly worse than the blank it replaced.
    """
    stored = _stored_chat({"done": True, "error": _terminal_error(),
                           "content": ""})
    payload = _client_payload(stored, {"error": _ABSENT})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert _wrote_nothing(driven)
    assert _only_marker(driven)["outcome"] == OUTCOME_REJECTED


def test_a_stale_tab_cannot_rewrite_the_stored_banner():
    stored = _stored_chat({"done": True, "error": _terminal_error(),
                           "content": ""})
    payload = _client_payload(
        stored, {"error": {"content": "something the client made up"}})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert _wrote_nothing(driven)


def test_a_stale_tab_cannot_claim_a_running_turn_finished():
    """The direction nobody asks for, and the one that lies to the reader.

    A tab that never saw the turn re-open sends `done: true` over a generation
    that is still running. The row then claims a finished answer while the
    model is mid-sentence -- the same "confident answer that is not an answer"
    class this stack tracks, arriving from the browser instead of the model.
    """
    stored = _stored_chat({"done": False, "content": ""})
    payload = _client_payload(stored, {"done": True})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert _wrote_nothing(driven)


def test_a_client_cannot_relabel_a_message_to_escape_the_guard():
    """The STORED role decides. Reading it from the payload is a bypass."""
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, {"role": "user", "done": False})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert _wrote_nothing(driven)


# --------------------------------------------------------------------------
# 2. the flows that must keep working
# --------------------------------------------------------------------------


def test_continue_generation_is_accepted():
    """Continue is legal BY CONSTRUCTION, not by exception.

    `continueResponse` never saves the chat -- it sets `done=false` in its own
    mirror and calls the completions socket. The server then re-opens the
    stored message through its own placeholder write. So by the time any
    full-blob save arrives, the STORED state is already the new turn's opening
    state and the client's copy agrees with it.
    """
    stored = _stored_chat({"done": False, "content": "the partial so far"})
    payload = _client_payload(stored, {"done": False})

    driven = _drive(stored, payload)

    assert driven.status is None
    assert len(driven.chats.blob_writes) == 1
    assert len(driven.chats.reconciles) == 1
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_regenerate_is_accepted():
    """A new sibling id; the old answer keeps `done: true` untouched."""
    stored = _stored_chat({"done": True})
    regenerated = {
        "id": "a-87654321",
        "parentId": USER_MSG,
        "childrenIds": [],
        "role": "assistant",
        "content": "",
        "done": False,
        "model": "synthetic-model",
    }
    payload = _client_payload(
        stored, extra_messages={"a-87654321": regenerated},
        current_id="a-87654321")

    driven = _drive(stored, payload)

    assert driven.status is None
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED
    assert "a-87654321" in driven.chats.blob_writes[0]["history"]["messages"]


def test_edit_and_resubmit_is_accepted():
    """New user id AND new assistant id -- a new branch, nothing overwritten."""
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, extra_messages={
        "u-87654321": {
            "id": "u-87654321", "parentId": None, "childrenIds": ["a-87654321"],
            "role": "user", "content": "the edited question",
        },
        "a-87654321": {
            "id": "a-87654321", "parentId": "u-87654321", "childrenIds": [],
            "role": "assistant", "content": "", "done": False,
            "model": "synthetic-model",
        },
    }, current_id="a-87654321")

    driven = _drive(stored, payload)

    assert driven.status is None
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_branch_switch_is_accepted_and_moves_current_id():
    """The highest-frequency carrier of a whole history: one arrow click.

    `showMessage` sets `history.currentId` and pushes the ENTIRE history. It
    changes no server-owned field, so it must pass -- and the field it does own
    must actually land, or the guard has broken navigation while looking safe.
    """
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, current_id=USER_MSG)

    driven = _drive(stored, payload)

    assert driven.status is None
    assert driven.chats.blob_writes[0]["history"]["currentId"] == USER_MSG
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_stop_is_accepted_after_the_server_has_written_its_rescue():
    """`save_interrupted_state` writes `done=True` FIRST; the client then agrees.

    The durable write goes before the notification on that path by design, so
    the ordering this test encodes is the one the server actually produces.
    """
    stored = _stored_chat({"done": True, "content": "the partial the user read"})
    payload = _client_payload(stored, {"done": True})

    driven = _drive(stored, payload)

    assert driven.status is None
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_a_second_tab_that_saw_the_turn_is_accepted():
    """Multi-tab is not the defect; a STALE mirror is. A caught-up second tab
    saves exactly what the server holds, and must not be punished for existing.
    """
    stored = _stored_chat({"done": True, "error": _terminal_error(),
                           "content": ""})
    payload = _client_payload(stored, {"done": True,
                                       "error": _terminal_error()})

    driven = _drive(stored, payload)

    assert driven.status is None
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_a_title_only_save_is_accepted_and_keeps_the_stored_history():
    """A rename, a tag edit and the controls autosave send NO history at all.

    This is why the comparison runs against the MERGED chat and not against the
    raw payload: compare the payload and every one of these looks like a client
    that dropped every message in the chat, which would 409 the most ordinary
    saves in the product.
    """
    stored = _stored_chat({"done": True, "error": _terminal_error()})

    driven = _drive(stored, {"title": "a new title"})

    assert driven.status is None
    written = driven.chats.blob_writes[0]
    assert written["title"] == "a new title"
    assert written["history"]["messages"][ASSISTANT_MSG]["done"] is True
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_a_legacy_assistant_message_without_done_is_not_guarded():
    """A field the server never wrote is a field the server cannot defend.

    Stated as a test rather than a comment because the alternative is worse than
    the gap: guarding an absent key would 409 every future save of the oldest
    chats and leave their owners unable to save them at all.
    """
    stored = _stored_chat({"done": _ABSENT})
    payload = _client_payload(stored, {"done": True})

    driven = _drive(stored, payload)

    assert driven.status is None
    assert _only_marker(driven)["outcome"] == OUTCOME_ACCEPTED


def test_a_different_spelling_of_done_is_not_staleness():
    """`done` is a flag, not a value. `1` and `True` are the same state."""
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, {"done": 1})

    driven = _drive(stored, payload)

    assert driven.status is None


def test_message_deletion_is_still_applied():
    """DECLARED NON-COVERAGE, pinned so it cannot become coverage by accident.

    A stored id absent from the payload is a real user action, and this guard
    does not refuse it -- which also means the 35 blob-lost message ids measured
    on this database are NOT closed by this patch.
    """
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, drop=(ASSISTANT_MSG,))

    driven = _drive(stored, payload)

    assert driven.status is None
    assert ASSISTANT_MSG not in driven.chats.reconciles[0]


# --------------------------------------------------------------------------
# 3. the server's own write path must stay open  (real database)
# --------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(ChatMessage.__table__.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_servers_own_placeholder_write_still_reopens_a_finished_turn(
    db_sessionmaker,
):
    """Continue-generation, at the layer that actually performs it.

    `main.py`'s existing-chat placeholder loop writes exactly this dict onto an
    EXISTING assistant message id, with no continue-aware branch anywhere near
    it, and `continue_response` is enabled for every group on live 8083. It
    reaches `ChatMessages.upsert_message`, whose present-key semantics move the
    row's `done` back to 0.

    THIS IS THE TEST THAT SAYS WHERE THE GUARD IS NOT. If a monotonic rule ever
    migrates into the shared write path -- which is where a reviewer's instinct
    puts it -- this goes red, and it goes red against a real row in a real
    database rather than against a mock that would have agreed with the mistake.
    """
    async with db_sessionmaker() as session:
        await ChatMessages.upsert_message(
            message_id=ASSISTANT_MSG, chat_id=CHAT_ID, user_id=USER_ID,
            data={"role": "assistant", "content": "a finished answer",
                  "done": True},
            db=session,
        )
        finished = await ChatMessages.get_message_by_id(
            f"{CHAT_ID}-{ASSISTANT_MSG}", db=session)
        assert finished.done is True, "positive control: the row was never done"

        reopened = await ChatMessages.upsert_message(
            message_id=ASSISTANT_MSG, chat_id=CHAT_ID, user_id=USER_ID,
            data={
                "id": ASSISTANT_MSG, "parentId": USER_MSG, "childrenIds": [],
                "role": "assistant", "content": "", "done": False,
                "model": "synthetic-model", "timestamp": 1_700_000_000,
            },
            db=session,
        )

    assert reopened.done is False, (
        "the server can no longer re-open a finished turn: continue-generation "
        "is broken, and it fails silently -- the row would claim done=true "
        "while a generation is actively running"
    )


# --------------------------------------------------------------------------
# 4. one merge, both stores
# --------------------------------------------------------------------------


def test_both_stores_are_fed_the_same_merged_history():
    """The `if messages:` split is how the two stores were observed to diverge.

    Feeding the reconcile from the same object the blob write received is what
    makes a future edit to one branch unable to reintroduce that split.
    """
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, current_id=USER_MSG)

    driven = _drive(stored, payload)

    assert driven.chats.reconciles[0] is (
        driven.chats.blob_writes[0]["history"]["messages"])


def test_the_content_rederivation_still_runs_on_the_merged_chat():
    """Pre-existing behaviour, kept: a changed `output` re-derives `content`.

    It moved into the pure merge, so it is asserted here rather than assumed.
    """
    stored = _stored_chat({"done": True, "output": [{"type": "text",
                                                     "text": "old"}]})
    payload = _client_payload(stored, {"output": [{"type": "text",
                                                   "text": "new"}]})

    driven = _drive(stored, payload)

    assert driven.status is None
    rederived = driven.chats.blob_writes[0]["history"]["messages"][ASSISTANT_MSG]
    assert rederived["content"] != "the stored answer"


def test_an_unchanged_output_does_not_rederive_content():
    stored = _stored_chat({"done": True, "output": [{"type": "text",
                                                     "text": "same"}]})
    payload = _client_payload(stored)

    driven = _drive(stored, payload)

    written = driven.chats.blob_writes[0]["history"]["messages"][ASSISTANT_MSG]
    assert written["content"] == "the stored answer"


def test_the_merge_is_pure():
    """MECHANISM, exercised with no framework at all: no I/O, no HTTP, no log.

    The conflict list is returned rather than raised, which is what lets policy
    (409 vs observe) live in the handler and be changed without touching this.
    """
    stored = _stored_chat({"done": True})
    payload = _client_payload(stored, {"done": False})

    merged, conflicts = _production("merge_client_chat")(stored, payload)

    assert [c["message_id"] for c in conflicts] == [ASSISTANT_MSG]
    assert conflicts[0]["field"] == "done"
    assert merged["history"]["currentId"] == ASSISTANT_MSG


def test_the_conflict_order_is_deterministic():
    """The marker names ONE message; which one must not depend on dict order."""
    stored = _stored_chat({"done": True})
    stored["history"]["messages"]["a-00000001"] = {
        "id": "a-00000001", "parentId": USER_MSG, "childrenIds": [],
        "role": "assistant", "content": "another answer", "done": True,
    }
    payload = _client_payload(stored, {"done": False})
    payload["history"]["messages"]["a-00000001"]["done"] = False

    first = _production("merge_client_chat")(stored, payload)[1]
    second = _production("merge_client_chat")(stored, dict(payload))[1]

    assert [c["message_id"] for c in first] == ["a-00000001", ASSISTANT_MSG]
    assert [c["message_id"] for c in second] == [c["message_id"] for c in first]


# --------------------------------------------------------------------------
# 5. observability: the rollout mode, and the positive control
# --------------------------------------------------------------------------


def test_observe_mode_applies_the_save_and_refuses_to_call_it_a_refusal():
    """The staged-rollout state, and it must never read as a refusal.

    The frontend has no 409 handler yet, so `enforce` costs a silently lost
    title/param/tag edit on any request it refuses. `observe` buys the
    measurement first -- but the undo STILL LANDS, and an operator who reads
    `would_reject` as `rejected` believes a save was stopped when it was
    applied.
    """
    stored = _stored_chat({"done": True, "error": _terminal_error()})
    payload = _client_payload(stored, {"done": False, "error": _ABSENT})

    driven = _drive(stored, payload, observe=True)

    assert driven.status is None
    assert len(driven.chats.blob_writes) == 1
    assert len(driven.chats.reconciles) == 1
    marker = _only_marker(driven)
    assert marker["outcome"] == OUTCOME_WOULD_REJECT
    assert marker["reason"] == REASON_STALE


def test_every_accepted_save_is_counted_too():
    """THE POSITIVE CONTROL, and the reason this kind has two outcomes.

    At eleven full-blob saves per forty-eight hours on live 8083, a
    rejection-only marker reads zero whether the guard is idle or was never
    deployed. This stack has already shipped a P0 fix that ran zero times while
    a report read its silence as health; the accept line is what makes the next
    zero mean something.
    """
    stored = _stored_chat({"done": True})

    driven = _drive(stored, _client_payload(stored))

    marker = _only_marker(driven)
    assert marker["outcome"] == OUTCOME_ACCEPTED
    assert marker["reason"] == REASON_NONE
    assert marker["chat"] == CHAT_ID
    # No message is named on an accept: there is no conflicting message, and
    # inventing one would put a real id in a record that means nothing about it.
    assert marker["msg"] == "-"


def test_the_producer_spells_the_vocabulary_the_consumers_were_written_against():
    """A rename here is a silently dropped signal, not a compile error.

    `journey_ledger._OWUI_MARKER_KINDS`, its `_STREAM_RE` alternation, its
    `LEDGER_EVENT_KINDS`, and the daily report's closed outcome/reason sets are
    all written against these exact bytes in another repository. Nothing in
    Python connects them, so this is the join.
    """
    assert _production("MARKER_CLIENT_SAVE") == MARKER_KIND
    assert _production("CLIENT_SAVE_ACCEPTED") == OUTCOME_ACCEPTED
    assert _production("CLIENT_SAVE_REJECTED") == OUTCOME_REJECTED
    assert _production("CLIENT_SAVE_WOULD_REJECT") == OUTCOME_WOULD_REJECT
    assert _production("CLIENT_SAVE_REASON_NONE") == REASON_NONE
    assert _production("CLIENT_SAVE_REASON_STALE_TURN_STATE") == REASON_STALE
    assert _production("ALLOWED_CLIENT_SAVE_OUTCOMES") == frozenset(
        {OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_WOULD_REJECT})
    assert _production("ALLOWED_CLIENT_SAVE_REASONS") == frozenset(
        {REASON_NONE, REASON_STALE})


def test_the_marker_carries_no_message_content():
    """The marker logger's whole trust is that nothing free-form reaches it."""
    secret = "the user's private question about their salary"
    stored = _stored_chat({"done": True})
    stored["history"]["messages"][USER_MSG]["content"] = secret
    stored["title"] = secret
    payload = _client_payload(stored, {"done": False})

    driven = _drive(stored, payload)

    assert driven.status == 409
    assert secret not in driven.raw[0]
    assert "salary" not in driven.raw[0]


def test_a_marker_field_containing_whitespace_is_refused():
    """`_KV_RE` splits on whitespace, so a space silently truncates the record."""
    with pytest.raises(ValueError):
        _production("build_client_save_marker")(
            "chat with a space", ASSISTANT_MSG,
            OUTCOME_REJECTED,
            REASON_STALE)


def test_an_outcome_outside_the_closed_set_is_refused():
    with pytest.raises(ValueError):
        _production("build_client_save_marker")(
            CHAT_ID, ASSISTANT_MSG, "sounds_fine",
            REASON_NONE)


def test_an_unbuildable_marker_is_still_emitted_and_claims_no_outcome():
    """A record that cannot be built must not vanish, and must not invent.

    Naming either real outcome in the fallback would claim a rejection that may
    not have happened, or an acceptance that may not have either.
    """
    line = _production("log_client_save")(
        "chat with a space", ASSISTANT_MSG,
        OUTCOME_ACCEPTED,
        REASON_NONE)

    fields = _fields(line)
    assert fields["kind"] == MARKER_KIND
    assert fields["outcome"] not in _production("ALLOWED_CLIENT_SAVE_OUTCOMES")
    assert fields["chat"] == "-"


def test_a_broken_marker_sink_cannot_break_the_save():
    """An observability write must not be able to break the request it watches."""

    class _Exploding(logging.Handler):
        def emit(self, record):
            raise RuntimeError("synthetic marker sink failure")

    logger = logging.getLogger("open_webui.utils.failure_surface")
    bomb = _Exploding()
    logger.addHandler(bomb)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        stored = _stored_chat({"done": True})
        driven = _drive(stored, _client_payload(stored))
    finally:
        logger.removeHandler(bomb)
        logger.setLevel(previous_level)

    assert driven.status is None
    assert len(driven.chats.blob_writes) == 1


# --------------------------------------------------------------------------
# 6. the consumer exists  (cross-repo, and skipped where it cannot be read)
# --------------------------------------------------------------------------


def _load_journey_ledger():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(
        here, "..", "..", "..", "..", "..", "scripts", "ops",
        "journey_ledger.py"))
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("journey_ledger_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ledger_in_this_repo_collects_this_marker():
    """"Emitted evidence nobody reads" is the failure mode this closes.

    A marker whose kind the collector drops is worse than no marker: the
    producer believes it is being watched. Skipped inside the container, where
    the ops repo is not mounted -- and a skip here is honestly "not measured",
    not "passed".
    """
    ledger = _load_journey_ledger()
    if ledger is None:
        pytest.skip("the ops repo is not reachable from this checkout")

    line = _production("build_client_save_marker")(
        CHAT_ID, ASSISTANT_MSG, OUTCOME_REJECTED,
        REASON_STALE)
    # The collector reads DOCKER's stream: an RFC3339 stamp, then loguru's own
    # line. A hand-written line missing the first field parses as nothing at
    # all, which is a green-looking way to test nothing.
    log_line = (
        "2026-08-17T00:00:00.000000000Z 2026-08-17 00:00:00.000 | INFO     | "
        "open_webui.utils.failure_surface:log_client_save:1 - " + line
    )

    record = ledger.parse_line(log_line, svc="owui")

    assert record is not None, "the collector drops this kind entirely"
    assert record["kind"] == MARKER_KIND
    assert record["outcome"] == OUTCOME_REJECTED
    assert record["reason"] == (
        REASON_STALE)
    assert record["chat"] == CHAT_ID
    assert record["msg"] == ASSISTANT_MSG


def test_only_the_identity_trusted_logger_may_file_this_kind():
    """Identity trust is per-logger. A kind carrying `chat`/`msg` from a module
    that also logs provider text is a forgery channel, and this is the same
    boundary every other owui marker kind already sits behind.
    """
    ledger = _load_journey_ledger()
    if ledger is None:
        pytest.skip("the ops repo is not reachable from this checkout")

    line = _production("build_client_save_marker")(
        CHAT_ID, ASSISTANT_MSG, OUTCOME_REJECTED,
        REASON_STALE)
    forged = (
        "2026-08-17T00:00:00.000000000Z 2026-08-17 00:00:00.000 | INFO     | "
        "open_webui.utils.middleware:x:1 - " + line
    )

    assert ledger.parse_line(forged, svc="owui") is None


def test_two_guarded_saves_in_one_chat_in_one_second_are_two_records():
    """The dedup key must carry the discriminator, or one of them disappears."""
    ledger = _load_journey_ledger()
    if ledger is None:
        pytest.skip("the ops repo is not reachable from this checkout")

    stamp = "2026-08-17T00:00:00.000000Z"
    accepted = {"svc": "owui", "kind": MARKER_KIND,
                "ts": stamp, "service": "owui", "chat": CHAT_ID,
                "outcome": OUTCOME_ACCEPTED,
                "reason": REASON_NONE, "msg": "-"}
    rejected = dict(accepted,
                    outcome=OUTCOME_REJECTED,
                    reason=REASON_STALE,
                    msg=ASSISTANT_MSG)

    assert ledger.record_key(accepted) != ledger.record_key(rejected)
    assert ledger.record_key(accepted) == ledger.record_key(dict(accepted))

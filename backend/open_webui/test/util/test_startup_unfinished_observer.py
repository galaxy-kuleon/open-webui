"""The startup observer must not write, checked at the REAL caller.

The existing `test_restart_reconcile.py` guards the pure helper: it proves
`count_unfinished` does not mutate the dicts handed to it. That is necessary and
it is not the boundary that broke.

The boundary that broke is `main._count_unfinished_turns`, which obtains rows
through `Chats` and could add a write of its own without touching the helper's
input or restoring any forbidden symbol:

    turns, chat_count, capped = restart_reconcile.count_unfinished(chats, now)
    if turns:
        await Chats.update_chat_by_id(chats[0].id, chats[0].chat)

That is invisible to every test that existed before this file, and it is
precisely the shape that was deployed and reverted on 2026-08-13 — a whole-chat
replacement that erased a concurrently arriving answer, split the embedded
history from the normalized message store, and bumped `updated_at`, which chat
lists sort on.

So this drives the real function with a synthetic repository whose only job is to
notice being asked to write.
"""
import asyncio
import copy
import logging
import unittest
from unittest import mock

import pytest

import open_webui.main as main


class _Chat:
    """The shape `Chats.get_chats()` returns: an id and an embedded chat dict."""

    def __init__(self, cid, ts):
        self.id = cid
        self.updated_at = ts
        self.chat = {
            "history": {
                "messages": {
                    "u1": {"role": "user", "content": "hello", "timestamp": ts},
                    "a1": {"role": "assistant", "content": "", "done": False,
                           "timestamp": ts, "model": "hermes-agent"},
                },
                "currentId": "a1",
            }
        }


class _Repo:
    """A repository that records what it is asked to do.

    Every method other than `get_chats` is a tripwire. `update_chat_by_id`
    simulates the dangerous HALF-completed write on purpose: it mutates the
    durable mirror and then raises, so a caller that writes is caught even
    though `_count_unfinished_turns` swallows exceptions.
    """

    def __init__(self, chats, durable):
        self._chats = chats
        self.durable = durable
        self.calls = []

    async def get_chats(self, *a, **kw):
        self.calls.append("get_chats")
        return self._chats

    async def update_chat_by_id(self, cid, body):
        self.calls.append("update_chat_by_id")
        self.durable[cid] = copy.deepcopy(body)   # the first half lands...
        raise RuntimeError("simulated failure after the embedded write")

    async def upsert_message_to_chat_by_id_and_message_id(self, *a, **kw):
        self.calls.append("upsert_message")
        raise AssertionError("startup observer attempted a message write")

    def __getattr__(self, name):
        raise AssertionError(f"startup observer touched Chats.{name}")


NOW = 1_700_000_000.0


class StartupObserverIsReadOnlyTests(unittest.TestCase):
    def _run(self):
        chats = [_Chat("c1", NOW - 60), _Chat("c2", NOW - 30)]
        durable = {c.id: copy.deepcopy(c.chat) for c in chats}
        repo = _Repo(chats, durable)
        before_objects = copy.deepcopy([c.chat for c in chats])
        before_durable = copy.deepcopy(durable)
        before_updated = [c.updated_at for c in chats]

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        main.log.addHandler(handler)
        # The observation is logged at INFO. A bare container has no logging
        # configured, so `open_webui.main` inherits an effective WARNING and the
        # line never reaches a handler -- the first version of this test read
        # that as "the observation was not made". The live deployment DOES run
        # INFO for this logger (verified against the running container), so the
        # level is a property of this fixture, not of the code under test.
        previous_level = main.log.level
        main.log.setLevel(logging.INFO)
        try:
            with mock.patch.object(main, "Chats", repo), \
                    mock.patch.object(main.time, "time", lambda: NOW):
                result = asyncio.run(main._count_unfinished_turns(mock.MagicMock()))
        finally:
            main.log.removeHandler(handler)
            main.log.setLevel(previous_level)
        return {
            "result": result, "repo": repo, "chats": chats, "durable": durable,
            "before_objects": before_objects, "before_durable": before_durable,
            "before_updated": before_updated, "records": records,
        }

    def test_it_counts_both_unfinished_turns(self):
        """The count is load-bearing for every other assertion here.

        `_count_unfinished_turns` catches every exception and returns 0, so a
        tripwire alone can be swallowed and leave a broken path looking green.
        A result of 2 proves the real path ran to its intended end.
        """
        self.assertEqual(self._run()["result"], 2)

    def test_it_never_asks_the_repository_to_write(self):
        out = self._run()
        self.assertEqual(out["repo"].calls, ["get_chats"])

    def test_no_chat_object_or_durable_mirror_changed(self):
        out = self._run()
        self.assertEqual([c.chat for c in out["chats"]], out["before_objects"])
        self.assertEqual(out["durable"], out["before_durable"])

    def test_updated_at_is_untouched(self):
        # The reverted sweep bumped this, and chat lists sort on it -- so every
        # swept chat jumped to the top of its owner's list. A read-only observer
        # that reorders someone's sidebar is not read-only.
        out = self._run()
        self.assertEqual([c.updated_at for c in out["chats"]],
                         out["before_updated"])

    def test_it_reports_exactly_what_it_saw(self):
        out = self._run()
        said = [r for r in out["records"] if "unfinished_turns_at_startup" in r]
        self.assertEqual(len(said), 1)
        for token in ("turns=2", "chats=2", "capped=no"):
            self.assertIn(token, said[0])


if __name__ == "__main__":
    unittest.main()

"""The startup reconciler: what it terminalizes, and everything it refuses.

Loaded by file path like its siblings so it runs without container deps.
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(__file__)


def _load(name):
    path = os.path.normpath(os.path.join(_HERE, "..", "..", "utils", name + ".py"))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fs = _load("failure_surface")
import sys

sys.modules.setdefault("open_webui", type(sys)("open_webui"))
sys.modules.setdefault("open_webui.utils", type(sys)("open_webui.utils"))
sys.modules["open_webui.utils"].failure_surface = fs
rr = _load("restart_reconcile")

NOW = 1_700_000_000.0


def _msg(**kw):
    base = {"role": "assistant", "content": "", "done": False,
            "timestamp": NOW - 60}
    base.update(kw)
    return base


class _Chat:
    def __init__(self, cid, messages):
        self.id = cid
        self.chat = {"history": {"messages": messages, "currentId": "keep-me"}}


class LooksUnfinishedTests(unittest.TestCase):
    """THE DEFECT: a turn whose process died stayed done=false forever.

    Measured on live 8083 on 2026-08-13 -- three such turns, two of them landing
    59s and 1m34s after the container was recreated under a real user. No
    content, no error, no ledger record: indistinguishable from a turn that
    never happened.
    """

    def test_the_abandoned_shape_is_counted(self):
        self.assertTrue(rr.looks_unfinished(_msg(), NOW))

    def test_a_turn_with_content_is_left_alone(self):
        # The user got something. Whatever happened next, it was not nothing.
        self.assertFalse(rr.looks_unfinished(_msg(content="partial answer"), NOW))

    def test_a_finished_turn_is_left_alone(self):
        self.assertFalse(rr.looks_unfinished(_msg(done=True), NOW))

    def test_a_turn_that_already_explains_itself_is_left_alone(self):
        # An error means some finalizer reached it and said why. Overwriting
        # that would replace a specific cause with a general one.
        self.assertFalse(
            rr.looks_unfinished(_msg(error={"cause": "stream_interrupted"}), NOW))

    def test_a_user_message_is_never_touched(self):
        self.assertFalse(rr.looks_unfinished(_msg(role="user"), NOW))

    def test_an_old_turn_is_history_not_a_pending_answer(self):
        old = _msg(timestamp=NOW - rr.RECONCILE_LOOKBACK_SECONDS - 1)
        self.assertFalse(rr.looks_unfinished(old, NOW))

    def test_a_turn_with_no_timestamp_is_refused(self):
        # The lookback is the only bound this sweep has. A row it cannot place
        # in time is a row it cannot promise not to rewrite from last month.
        self.assertFalse(rr.looks_unfinished(_msg(timestamp=None), NOW))

    def test_a_turn_from_the_future_is_refused(self):
        # Clock skew, not a pending answer. Negative age fails the bound.
        self.assertFalse(rr.looks_unfinished(_msg(timestamp=NOW + 600), NOW))


class CountUnfinishedTests(unittest.TestCase):
    """It COUNTS. The mutating version of this was reverted the day it shipped.

    Round 62 returned NO-PASS on four boundaries: `redis is None` is not an
    ownership test (UVICORN_WORKERS is configurable and a second container on
    the same volume is invisible), the whole-chat replacement erased a
    concurrently arriving answer in a race probe, it bypassed the normalized
    `chat_message` store, and `update_chat_by_id` bumps `updated_at` -- which
    chat lists sort on, so every swept chat jumped to the top of its owner's
    list.
    """

    def test_it_counts_the_unfinished_turn_and_only_that_one(self):
        chats = [_Chat("c1", {"a": _msg(), "b": _msg(done=True),
                              "c": _msg(content="hi"), "u": _msg(role="user")})]
        self.assertEqual(rr.count_unfinished(chats, NOW), (1, 1, False))

    def test_the_cap_says_it_capped(self):
        # A truncated count that does not say so reads exactly like a complete
        # one, and the operator cannot tell a quiet night from an early stop.
        many = {str(i): _msg() for i in range(50)}
        turns, _, capped = rr.count_unfinished([_Chat("c1", many)], NOW, limit=10)
        self.assertEqual(turns, 10)
        self.assertTrue(capped)

    def test_an_uncapped_count_says_so(self):
        turns, _, capped = rr.count_unfinished([_Chat("c1", {"a": _msg()})], NOW)
        self.assertEqual((turns, capped), (1, False))

    def test_a_malformed_chat_does_not_stop_the_count(self):
        class Broken:
            id = "b"
            chat = "not-a-dict"

        self.assertEqual(
            rr.count_unfinished([Broken(), _Chat("c1", {"a": _msg()})], NOW),
            (1, 1, False))

    def test_counting_does_not_MODIFY_what_it_counts(self):
        """A read-only function must leave its input identical.

        Registered after a mutant that set `done=True` on each counted message
        SURVIVED: the count was unchanged, so nothing noticed the write. In the
        live caller those dicts are the chat body about to be handed back, so an
        in-memory mutation here is the first half of exactly the destructive
        boundary this module was reverted for.
        """
        import copy

        chats = [_Chat("c1", {"a": _msg(), "b": _msg(done=True),
                              "c": _msg(content="hi")})]
        before = copy.deepcopy(chats[0].chat)
        rr.count_unfinished(chats, NOW)

        self.assertEqual(chats[0].chat, before)

    def test_the_module_can_no_longer_write_anything(self):
        # THE REVERT, pinned. A future edit that re-adds a mutating helper here
        # has to delete this test to do it, which is a visible decision rather
        # than a quiet reintroduction of a destructive boundary.
        for gone in ("build_terminal_message", "plan_reconciliation",
                     "sole_instance", "should_terminalize"):
            self.assertFalse(hasattr(rr, gone), gone)


if __name__ == "__main__":
    unittest.main()

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


class ShouldTerminalizeTests(unittest.TestCase):
    """THE DEFECT: a turn whose process died stayed done=false forever.

    Measured on live 8083 on 2026-08-13 -- three such turns, two of them landing
    59s and 1m34s after the container was recreated under a real user. No
    content, no error, no ledger record: indistinguishable from a turn that
    never happened.
    """

    def test_the_abandoned_shape_is_terminalized(self):
        self.assertTrue(rr.should_terminalize(_msg(), NOW))

    def test_a_turn_with_content_is_left_alone(self):
        # The user got something. Whatever happened next, it was not nothing.
        self.assertFalse(rr.should_terminalize(_msg(content="partial answer"), NOW))

    def test_a_finished_turn_is_left_alone(self):
        self.assertFalse(rr.should_terminalize(_msg(done=True), NOW))

    def test_a_turn_that_already_explains_itself_is_left_alone(self):
        # An error means some finalizer reached it and said why. Overwriting
        # that would replace a specific cause with a general one.
        self.assertFalse(
            rr.should_terminalize(_msg(error={"cause": "stream_interrupted"}), NOW))

    def test_a_user_message_is_never_touched(self):
        self.assertFalse(rr.should_terminalize(_msg(role="user"), NOW))

    def test_an_old_turn_is_history_not_a_pending_answer(self):
        old = _msg(timestamp=NOW - rr.RECONCILE_LOOKBACK_SECONDS - 1)
        self.assertFalse(rr.should_terminalize(old, NOW))

    def test_a_turn_with_no_timestamp_is_refused(self):
        # The lookback is the only bound this sweep has. A row it cannot place
        # in time is a row it cannot promise not to rewrite from last month.
        self.assertFalse(rr.should_terminalize(_msg(timestamp=None), NOW))

    def test_a_turn_from_the_future_is_refused(self):
        # Clock skew, not a pending answer. Negative age fails the bound.
        self.assertFalse(rr.should_terminalize(_msg(timestamp=NOW + 600), NOW))


class SoleInstanceTests(unittest.TestCase):
    def test_it_refuses_to_run_when_another_instance_could_be_serving(self):
        # THE SAFETY ARGUMENT. With Redis configured another worker may be
        # mid-answer on a turn this process can see, and marking that turn
        # interrupted would destroy a live answer. Not knowing is not
        # permission.
        self.assertFalse(rr.sole_instance(redis_configured=True))

    def test_it_runs_when_nothing_else_can_be(self):
        self.assertTrue(rr.sole_instance(redis_configured=False))


class PlanTests(unittest.TestCase):
    def test_it_finds_the_abandoned_turn_and_only_that_one(self):
        chats = [_Chat("c1", {"a": _msg(), "b": _msg(done=True),
                              "c": _msg(content="hi"), "u": _msg(role="user")})]
        planned = rr.plan_reconciliation(chats, NOW)

        self.assertEqual([(c, m) for c, m, _ in planned], [("c1", "a")])

    def test_the_cap_bounds_a_bad_predicate(self):
        many = {str(i): _msg() for i in range(50)}
        planned = rr.plan_reconciliation([_Chat("c1", many)], NOW, limit=10)

        self.assertEqual(len(planned), 10)

    def test_a_malformed_chat_does_not_stop_the_sweep(self):
        class Broken:
            id = "b"
            chat = "not-a-dict"

        planned = rr.plan_reconciliation([Broken(), _Chat("c1", {"a": _msg()})], NOW)
        self.assertEqual([(c, m) for c, m, _ in planned], [("c1", "a")])


class TerminalMessageTests(unittest.TestCase):
    def test_it_carries_the_restart_cause_and_a_banner(self):
        out = rr.build_terminal_message(_msg(), "chat1234", "msg45678")

        self.assertTrue(out["done"])
        self.assertEqual(out["error"]["cause"], "interrupted_by_restart")
        self.assertIn("interrupted_by_restart", out["error"]["content"])
        self.assertIn(out["error"]["trace_id"], out["error"]["content"])

    def test_it_preserves_every_other_field(self):
        # A sweep that drops `model` or `parentId` breaks the thread the user
        # reads, in exchange for a banner.
        original = _msg(model="hermes-agent", parentId="p1", childrenIds=[])
        out = rr.build_terminal_message(original, "c", "m")

        for key in ("model", "parentId", "childrenIds", "role", "timestamp"):
            self.assertEqual(out[key], original[key], key)

    def test_the_banner_goes_through_the_shared_builder(self):
        # Not a second banner composed here: one place builds the sentence, so
        # the two cannot drift and the M4 boundary is enforced once.
        out = rr.build_terminal_message(_msg(), "chat1234", "msg45678")
        expected = fs.build_error_payload(
            "chat1234", "msg45678", cause=fs.CAUSE_INTERRUPTED_BY_RESTART)
        self.assertEqual(out["error"], expected)


if __name__ == "__main__":
    unittest.main()

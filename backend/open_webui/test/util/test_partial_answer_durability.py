"""What the user already read must survive the stream dying.

`ENABLE_REALTIME_CHAT_SAVE` defaults to False, and `flush_pending_delta_data`
emits a socket event and touches no database — so while a stream runs, the
answer exists only in the browser. It is written to the chat exactly once, at
the end.

There were two ends. The normal one, and `except asyncio.CancelledError`, which
persists partial output. Everything else — an upstream truncating its chunked
transfer, a provider dropping mid-answer — escaped to the outer handler in
`main.py`, which writes ONLY `{'parentId', 'error'}`: no `content`, no `done`.

The first fix put a catch around the WHOLE handler. It worked for the initial
stream and was wrong twice over: it covered 1,400 lines of events, DB writes and
background work rather than the thing that actually breaks, and it never fired
for the tool-loop or code-interpreter continuations, whose own broad
`except Exception` — written for setup errors — swallowed the dying stream
first. A tool-using turn is the ordinary case. Two thirds of real traffic kept
losing the text, under a test suite that passed.

So the rule these tests protect is narrower and checkable: **every site that
consumes a provider body goes through one boundary that persists what the user
already saw, and no closer handler may swallow that failure.**

Structural on purpose — the behaviour lives inside a 1,400-line closure over a
live event emitter, a provider response and a chat DB. What a reader (or the
next refactor) can check without any of that is the grammar.
"""

import ast
import unittest
from pathlib import Path

MIDDLEWARE = Path(__file__).resolve().parents[2] / "utils" / "middleware.py"


class PartialAnswerDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.src = MIDDLEWARE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)

    def _calls(self, name):
        return [n for n in ast.walk(self.tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]

    def _enclosing_tries(self, line):
        return sorted(
            (t.lineno, t.end_lineno,
             [ast.unparse(h.type) if h.type else "bare" for h in t.handlers])
            for t in ast.walk(self.tree)
            if isinstance(t, ast.Try) and t.lineno <= line <= t.end_lineno
        )

    def test_no_provider_body_is_consumed_outside_the_durable_boundary(self):
        """The hole the first fix left: two of three consumption sites."""
        raw = [n.lineno for n in self._calls("stream_body_handler")]
        boundary = [n for n in ast.walk(self.tree)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and n.name == "consume_stream_durably"]
        self.assertEqual(len(boundary), 1, "the durable boundary is gone")
        inside = [l for l in raw
                  if boundary[0].lineno <= l <= boundary[0].end_lineno]
        self.assertEqual(
            sorted(raw), sorted(inside),
            f"stream_body_handler is consumed directly at lines "
            f"{sorted(set(raw) - set(inside))} — those turns lose whatever the "
            f"user had already read",
        )

    def test_every_consumption_site_actually_uses_it(self):
        sites = self._calls("consume_stream_durably")
        self.assertGreaterEqual(
            len(sites), 3,
            "there are three provider-body consumption sites (initial, tool "
            "loop, code interpreter); found "
            f"{len(sites)} going through the boundary",
        )

    def test_no_closer_handler_swallows_a_dying_stream(self):
        """The two continuations sit inside their own `except Exception`. If it
        does not re-raise StreamBodyInterrupted, the turn falls through to the
        normal done=True finalisation and the partial answer is lost — which is
        exactly what happened while the previous suite was green."""
        for call in self._calls("consume_stream_durably"):
            for start, end, handlers in self._enclosing_tries(call.lineno):
                if "Exception" in handlers or "bare" in handlers:
                    self.assertIn(
                        "StreamBodyInterrupted", handlers,
                        f"the try at line {start} around the call at "
                        f"{call.lineno} catches Exception without re-raising "
                        f"StreamBodyInterrupted",
                    )

    def test_the_boundary_persists_before_it_reraises(self):
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "consume_stream_durably"][0]
        saves = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "save_interrupted_state"]
        self.assertTrue(saves, "the boundary never persists anything")
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        self.assertTrue(raises, "the boundary swallows the failure entirely")

    def test_what_is_saved_is_what_the_user_SAW(self):
        """`output` alone is not it. During a tool or code-interpreter
        continuation the earlier visible content is moved into `prior_output`
        and restored only afterwards, so saving bare `output` drops exactly the
        tool history already on screen."""
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "save_interrupted_state"][0]
        body = ast.unparse(fn)
        self.assertIn("full_output()", body,
                      "the interrupted save does not include prior_output")
        self.assertNotIn(
            "'output': output", body,
            "the interrupted save writes bare `output`, dropping the tool "
            "history the user had already read",
        )

    def test_a_transport_failure_is_not_reported_as_user_cancellation(self):
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "save_interrupted_state"][0]
        self.assertTrue(fn.args.args, "the save takes no reason argument")
        reasons = set()
        for call in self._calls("save_interrupted_state"):
            for arg in call.args:
                if isinstance(arg, ast.Constant):
                    reasons.add(arg.value)
        self.assertEqual(
            reasons, {"cancelled", "stream_failed"},
            f"callers pass {sorted(reasons)} — a transport failure counted as "
            f"a user cancellation is a fault that disappears from every number "
            f"that matters",
        )

    def test_the_save_is_visible_from_every_caller(self):
        """It used to be defined INSIDE the CancelledError handler. Any sibling
        caller would raise NameError — swallowed by the surrounding
        `except (asyncio.CancelledError, Exception): pass`, leaving nothing
        saved and no trace of why."""
        defs = [n for n in ast.walk(self.tree)
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                and n.name == "save_interrupted_state"]
        self.assertEqual(len(defs), 1)
        # Definition ORDER does not matter — these are nested defs evaluated
        # before any of them runs. What matters is that the definition is not
        # buried inside an exception handler, because then it only exists for
        # whoever entered that handler.
        buried = [h.lineno for t in ast.walk(self.tree)
                  if isinstance(t, ast.Try)
                  for h in t.handlers
                  if h.lineno <= defs[0].lineno <= h.end_lineno]
        self.assertEqual(
            buried, [],
            f"the durable save is defined inside the except handler(s) at "
            f"{buried}; any other caller gets NameError, and the guard around "
            f"it swallows that silently")

    def test_done_true_is_not_a_claim_of_completeness(self):
        fn = [n for n in ast.walk(self.tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "save_interrupted_state"][0]
        seg = self.src.splitlines()[fn.lineno - 1:fn.end_lineno]
        self.assertTrue(
            any("NOT a claim" in l or "not a claim" in l for l in seg),
            "done=True closes the spinner and must not be read as a finished "
            "answer; say so where it is set",
        )

    def test_the_name_does_not_promise_only_cancellation(self):
        stale = [n for n in ast.walk(self.tree)
                 if (isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                     and n.name == "save_cancelled_state")
                 or (isinstance(n, ast.Call)
                     and getattr(n.func, "id", "") == "save_cancelled_state")]
        self.assertEqual(stale, [], "a stream that DIED is not one that was cancelled")


if __name__ == "__main__":
    unittest.main()

"""What the user already read must survive the stream dying.

`ENABLE_REALTIME_CHAT_SAVE` defaults to False, and `flush_pending_delta_data`
emits a socket event and touches no database — so during a stream the answer
exists only in the browser. It is written to the chat exactly once, at the end.

There were two ends. The normal one, and `except asyncio.CancelledError`, which
persists partial output. Anything else — an upstream that truncates its chunked
transfer, a provider dropping mid-answer — escaped to the outer handler in
`main.py`, which writes ONLY `{'parentId', 'error'}`: no `content`, no `done`.
The user watched an answer arrive, the stream broke, and the text they had
already read was never anywhere but their screen. Reload, and an error stands
where their partial answer used to be.

These are structural assertions on purpose. The behaviour lives inside a
1400-line closure over a live event emitter, a provider response and a chat DB;
the property worth protecting is that BOTH failure branches reach the same
durable save, and that is exactly what a reader — or the next refactor — can
check without any of that.
"""

import ast
import unittest
from pathlib import Path

MIDDLEWARE = (
    Path(__file__).resolve().parents[2] / "utils" / "middleware.py"
)


def _streaming_try(tree):
    """The try that guards the whole streaming body."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        names = [ast.unparse(h.type) for h in node.handlers if h.type]
        if "asyncio.CancelledError" in names:
            return node
    return None


class PartialAnswerDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.src = MIDDLEWARE.read_text(encoding="utf-8")
        self.tree = ast.parse(self.src)

    def test_a_dying_stream_is_handled_at_all(self):
        node = _streaming_try(self.tree)
        self.assertIsNotNone(node, "the streaming try block is gone")
        names = [ast.unparse(h.type) for h in node.handlers if h.type]
        self.assertIn(
            "Exception", names,
            "only CancelledError is handled, so an upstream that truncates "
            "loses everything the user had already read",
        )

    def test_both_failure_branches_reach_the_same_durable_save(self):
        node = _streaming_try(self.tree)
        for handler in node.handlers:
            label = ast.unparse(handler.type) if handler.type else "bare"
            calls = [
                n for n in ast.walk(handler)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "save_interrupted_state"
            ]
            self.assertTrue(
                calls,
                f"the `except {label}` branch never persists what the user "
                f"already saw",
            )

    def test_the_save_is_visible_from_both_branches(self):
        """It used to be defined INSIDE the CancelledError handler. A sibling
        handler calling it would raise NameError — which the surrounding
        `except (asyncio.CancelledError, Exception): pass` would swallow,
        leaving nothing saved and no trace of why."""
        node = _streaming_try(self.tree)
        defs = [
            n for n in ast.walk(self.tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == "save_interrupted_state"
        ]
        self.assertEqual(len(defs), 1, "expected exactly one definition")
        self.assertLess(
            defs[0].end_lineno, node.lineno,
            "the durable save is defined inside the try it is supposed to "
            "rescue; at least one branch cannot see it",
        )

    def test_the_name_does_not_promise_only_cancellation(self):
        """AST, not text: the comment above the helper mentions the old name on
        purpose, to explain why it changed. A grep-based assertion would fail on
        its own explanation."""
        stale = [
            n for n in ast.walk(self.tree)
            if (isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                and n.name == "save_cancelled_state")
            or (isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "save_cancelled_state")
        ]
        self.assertEqual(
            stale, [],
            "the helper serves cancellation AND failure; a reader who trusts "
            "the old name assumes the failure path has no durability story",
        )


if __name__ == "__main__":
    unittest.main()

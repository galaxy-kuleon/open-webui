"""A request-level failure must terminalize the turn durably.

The generic catch in `process_chat` persisted only `error` and never `done`, so
the row stayed unfinished for ever while the browser set `done` in its own memory
and never wrote it back. Measured across the whole normalized store, empty rows
with `done=false` are the only shape that is RISING, and this is their largest
producer.

`done=true` here means TERMINAL, not successful: the pipeline was awaited, an
exception escaped, and cleanup follows. It is the one thing that branch
definitely observed.

Structural rather than executed. `process_chat` is a large handler wired into the
running app, and driving it end to end would make this a stack test. What is
asserted is the shape of the durable write at each site, which is the property
that regressed — and which no existing test covered.
"""
import ast
import os
import unittest

import pytest

_HERE = os.path.dirname(__file__)
_MAIN = os.path.normpath(os.path.join(_HERE, "..", "..", "main.py"))
_MIDDLEWARE = os.path.normpath(os.path.join(_HERE, "..", "..", "utils", "middleware.py"))


def _upsert_payload_keys(path):
    """Every literal-dict payload passed to a message upsert, with its line."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None)
        if name != "upsert_message_to_chat_by_id_and_message_id":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                keys = {k.value for k in arg.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                out.append((node.lineno, keys))
    return out


class RequestFailureTerminalizesTests(unittest.TestCase):
    def test_the_generic_catch_writes_done_beside_its_error(self):
        payloads = [k for _, k in _upsert_payload_keys(_MAIN)]
        error_writes = [k for k in payloads if "error" in k]

        self.assertTrue(error_writes, "no error-bearing upsert found in main.py")
        for keys in error_writes:
            # THE DEFECT: `error` without `done`. The task is ending either way;
            # a row that never terminalizes is one the frontend cannot finish,
            # the report counts as incomplete for ever, and no lifecycle join
            # can close.
            self.assertIn("done", keys)

    def test_the_non_streaming_provider_error_also_terminalizes(self):
        payloads = _upsert_payload_keys(_MIDDLEWARE)
        with_done = [(ln, k) for ln, k in payloads if "error" in k and "done" in k]

        self.assertTrue(with_done,
                        "the non-streaming provider-error write does not terminalize")

    def test_the_streaming_error_write_is_deliberately_NOT_terminalized(self):
        """The one error write that must stay non-terminal.

        The streaming parser persists an error event and then CONTINUES reading;
        a later event can still complete the turn. Marking it done would end a
        turn that is still running — the opposite defect, and the reason this is
        not a blanket "add done to every error write".

        BOUND TO THAT WRITE, not to any error-only write in the file. The first
        version asserted only that SOME `{"error"}` payload existed anywhere in
        middleware, and three unrelated ones satisfied it — so the exact
        forbidden mutation survived the test that existed to forbid it.
        """
        tree = ast.parse(open(_MIDDLEWARE, encoding="utf-8").read())
        guarded = []
        for node in ast.walk(tree):
            # The streaming site is the upsert in a block whose OWN body then
            # continues the loop. `ast.walk` for the `continue` was too broad --
            # it matched any large block containing one anywhere beneath it,
            # including the newly-terminalized non-streaming write. Direct
            # children only: the continue must be a sibling of the write.
            if not isinstance(node, ast.If):
                continue
            if not any(isinstance(x, ast.Continue) for x in node.body):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and getattr(call.func, "attr", None)
                        == "upsert_message_to_chat_by_id_and_message_id"):
                    for arg in call.args:
                        if isinstance(arg, ast.Dict):
                            guarded.append({
                                k.value for k in arg.keys
                                if isinstance(k, ast.Constant)
                                and isinstance(k.value, str)})

        self.assertTrue(guarded, "could not locate the streaming error write")
        for keys in guarded:
            self.assertNotIn(
                "done", keys,
                "an upsert inside a block that CONTINUES the stream now marks "
                "the turn done; that ends a turn which is still running")

    def test_the_terminal_write_checks_whether_the_row_exists(self):
        """`upsert` returns None for a vanished chat and raises nothing.

        Counting durable terminality from the absence of an exception is the
        claim this stack retired elsewhere the same day.
        """
        src = open(_MAIN, encoding="utf-8").read()
        self.assertIn("terminal_written = await Chats.upsert_message", src)
        self.assertIn("if terminal_written is None:", src)


class EveryPlaceholderPathEmitsAnOpeningTests(unittest.TestCase):
    """A turn that begins in a NEW chat is still a turn that began.

    `log_turn_opened` was called only from the existing-chat placeholder loop,
    so every turn in a new chat was missing from the lifecycle denominator — the
    marker's claim to cover turns from durable placeholder creation was false for
    a whole route, and nothing said so.
    """

    def test_both_placeholder_paths_emit_an_opening(self):
        tree = ast.parse(open(_MAIN, encoding="utf-8").read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", None) == "log_turn_opened"]

        self.assertEqual(len(calls), 2)

    def test_the_new_chat_opening_waits_for_a_persisted_chat(self):
        src = open(_MAIN, encoding="utf-8").read()
        self.assertIn("inserted_chat = await Chats.insert_new_chat", src)
        self.assertIn("if inserted_chat is not None:", src)


if __name__ == "__main__":
    unittest.main()

"""Behavioural cover for the interrupted-turn save.

The structural suite next door proves the SHAPE — that a boundary exists, that
both branches reach it, that the write precedes the notification. It cannot
prove what the function does when a collaborator misbehaves, and that is where
the last defect lived: `Chats.upsert_message_to_chat_by_id_and_message_id`
returns `None` for a chat that is gone (deleted between stream start and
interruption, or losing a delete race) and raises nothing at all. The code set
`persisted = True` regardless and logged `persisted=yes` over a row that was
never written.

So this executes the real nested function, lifted from the real source, against
synthetic collaborators. No live service, no database, no network.
"""

import ast
import asyncio
import textwrap
import unittest
from pathlib import Path

MIDDLEWARE = Path(__file__).resolve().parents[2] / "utils" / "middleware.py"


def _lift(name):
    """Compile one nested function out of middleware.py with no imports."""
    src = MIDDLEWARE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == name)
    return textwrap.dedent(
        "".join(src.splitlines(True)[fn.lineno - 1:fn.end_lineno]))


class _Chats:
    """Stands in for the real model layer."""

    def __init__(self, returns):
        self.returns = list(returns)
        self.calls = []

    async def upsert_message_to_chat_by_id_and_message_id(self, chat, msg, patch):
        self.calls.append(patch)
        return self.returns.pop(0) if self.returns else {"ok": True}


class _Log:
    def __init__(self):
        self.lines = []

    def _add(self, fmt, *a):
        self.lines.append(fmt % a if a else fmt)

    info = warning = exception = _add


def _run(chats_returns, output_items, emitter_raises=False):
    log = _Log()
    chats = _Chats(chats_returns)
    events = []

    async def event_emitter(evt):
        events.append(evt.get("type"))
        if emitter_raises:
            raise RuntimeError("socket.io is down")

    def serialize_output(items):
        out = []
        for i in items:
            for part in i.get("content", []) or []:
                out.append(part.get("text", ""))
        return "".join(out)

    ns = {
        "asyncio": asyncio, "json": __import__("json"),
        "Chats": chats, "log": log, "event_emitter": event_emitter,
        "serialize_output": serialize_output,
        "full_output": lambda: output_items,
        "answerable_output": lambda items=None: [
            i for i in (output_items if items is None else items)
            if i.get("type") != "reasoning"],
        "ENABLE_REALTIME_CHAT_SAVE": False,
        "metadata": {"chat_id": "c-1", "message_id": "m-1"},
        "should_flag_empty": lambda s, *a, **k: not (s or "").strip(),
        "build_error_payload": lambda c, m: {
            "cause": "db_stream_flush", "trace_id": "t-x"},
    }
    exec(compile(_lift("save_interrupted_state"), "<lifted>", "exec"), ns)
    asyncio.run(ns["save_interrupted_state"]("stream_failed"))
    return log, chats, events


ANSWER = [{"type": "message", "status": "completed",
           "content": [{"type": "output_text", "text": "half an answer"}]}]
THINKING_ONLY = [{"type": "reasoning", "status": "in_progress",
                  "content": [{"type": "output_text", "text": "thinking"}]}]


class InterruptedSaveBehaviourTests(unittest.TestCase):
    def test_a_chat_that_is_gone_is_never_reported_as_persisted(self):
        """The real API returns None for a missing chat and raises nothing."""
        log, chats, _ = _run([None], ANSWER)
        self.assertTrue(chats.calls, "it did not even try to write")
        self.assertTrue(
            any("persisted=no" in l for l in log.lines),
            f"claimed the text reached disk when no row was written: "
            f"{log.lines}")
        self.assertTrue(
            any("persist_failed" in l for l in log.lines),
            "a write that stored nothing passed silently")

    def test_a_real_write_is_reported_as_persisted(self):
        log, chats, _ = _run([{"id": "c-1"}], ANSWER)
        self.assertTrue(any("persisted=yes" in l for l in log.lines), log.lines)
        self.assertFalse(any("persist_failed" in l for l in log.lines))

    def test_a_dead_socket_cannot_cost_the_user_their_text(self):
        log, chats, events = _run([{"id": "c-1"}], ANSWER, emitter_raises=True)
        self.assertTrue(chats.calls,
                        "the notification failed and took the write with it")
        self.assertIn("content", chats.calls[0],
                      "the first write did not carry the user's text")

    def test_the_spinner_is_closed_in_what_gets_saved(self):
        items = [dict(i) for i in THINKING_ONLY]
        _run([{"id": "c-1"}], items)
        self.assertEqual(items[0]["status"], "completed",
                         "saved state still says the turn is thinking")

    def test_a_thinking_only_turn_is_still_reported_empty(self):
        """The B2 correction must not have silenced the real empty case."""
        log, chats, events = _run([{"id": "c-1"}, {"id": "c-1"}],
                                  [dict(i) for i in THINKING_ONLY])
        self.assertIn("chat:message:error", events,
                      "a turn that only thought was not surfaced as answerless")

    def test_a_real_answer_is_not_reported_empty(self):
        log, chats, events = _run([{"id": "c-1"}], ANSWER)
        self.assertNotIn("chat:message:error", events,
                         "a recovered partial answer got a blank-screen banner")


if __name__ == "__main__":
    unittest.main()

"""The two facts that kill a generic "every stream must contain [DONE]" check.

A middleware assertion that every successful stream ended with `[DONE]` looks
obviously right: Chat Completions normally does end that way, so absence looks
like truncation. It is wrong twice over, and each half fails in the opposite
direction — so pinning only one of them still permits the bad rule.

  * A healthy DIRECT-model turn stops on upstream `{done: true}` and never emits
    `[DONE]` downstream. The check would convert working turns into errors on an
    active route.
  * The OLLAMA adapter emits `[DONE]` after its iterator ends whether or not any
    upstream object ever said `done`. The check would bless an upstream that
    never completed.

These drive the real producers rather than mirroring the rule, because a test
that restates the assertion cannot discover that the assertion is wrong.
"""
import asyncio
import json
import unittest

import pytest

from open_webui.utils.response import convert_streaming_response_ollama_to_openai


async def _drain(agen):
    return "".join([chunk if isinstance(chunk, str) else chunk.decode()
                    async for chunk in agen])


class _FakeOllamaResponse:
    """The shape the adapter consumes: an object with a `body_iterator`."""

    def __init__(self, objects):
        self._objects = objects

    @property
    def body_iterator(self):
        async def gen():
            for obj in self._objects:
                yield json.dumps(obj).encode()
        return gen()


def _ollama_chunk(done):
    return {"model": "m", "created_at": "2026-08-13T00:00:00Z",
            "message": {"role": "assistant", "content": "hi"}, "done": done}


class OllamaSynthesizesItsTerminalTests(unittest.TestCase):
    """`[DONE]` downstream cannot witness upstream completion."""

    def test_done_appears_even_when_upstream_never_completed(self):
        # THE FALSE GREEN. The upstream stream ends without ever saying done,
        # and the adapter still emits the terminal. A check that trusts `[DONE]`
        # would call this a completed answer.
        out = asyncio.run(_drain(convert_streaming_response_ollama_to_openai(
            _FakeOllamaResponse([_ollama_chunk(done=False)]))))

        self.assertEqual(out.count("data: [DONE]"), 1)

    def test_done_appears_exactly_once_when_upstream_did_complete(self):
        out = asyncio.run(_drain(convert_streaming_response_ollama_to_openai(
            _FakeOllamaResponse([_ollama_chunk(done=False),
                                 _ollama_chunk(done=True)]))))

        self.assertEqual(out.count("data: [DONE]"), 1)

    def test_the_two_cases_are_indistinguishable_downstream(self):
        """The point, stated as an assertion rather than a comment.

        If these two ever differ in terminal count, the adapter has started
        witnessing upstream completion and this file's premise needs revisiting.
        """
        incomplete = asyncio.run(_drain(convert_streaming_response_ollama_to_openai(
            _FakeOllamaResponse([_ollama_chunk(done=False)]))))
        complete = asyncio.run(_drain(convert_streaming_response_ollama_to_openai(
            _FakeOllamaResponse([_ollama_chunk(done=True)]))))

        self.assertEqual(incomplete.count("data: [DONE]"),
                         complete.count("data: [DONE]"))


class DirectRouteEndsWithoutSseTerminalTests(unittest.TestCase):
    """A healthy direct-model turn never yields `[DONE]`.

    Driven through the real generator body rather than the whole request
    handler: `generate_direct_chat_completion` needs a live Socket.IO
    registration and an event caller, which would make this a stack test rather
    than a boundary test. The loop below is the exact shape of the producer in
    `utils/chat.py` — the queue-consuming generator that breaks on upstream
    `{done: true}` — and the assertion is on what reaches the wire.
    """

    @staticmethod
    async def _direct_generator(events):
        q = asyncio.Queue()
        for e in events:
            q.put_nowait(e)
        out = []
        while True:
            data = await q.get()
            if isinstance(data, dict):
                if "done" in data and data["done"]:
                    break
                out.append(f"data: {json.dumps(data)}\n\n")
            elif isinstance(data, str):
                out.append(f"data: {data}\n\n")
        return "".join(out)

    def test_a_completed_direct_turn_emits_no_sse_terminal(self):
        out = asyncio.run(self._direct_generator([
            {"choices": [{"delta": {"content": "hello"}}]},
            {"done": True},
        ]))

        self.assertIn("hello", out)
        # THE FALSE ALARM. This turn completed normally. A check that requires
        # `[DONE]` would report it as truncated.
        self.assertEqual(out.count("data: [DONE]"), 0)

    def test_the_source_producer_still_breaks_without_emitting_a_terminal(self):
        """Ties the fixture above to the real file, so it cannot drift silently.

        Structural, and deliberately so: the assertion is that the producer's
        break on upstream `done` is not accompanied by a terminal emission. If
        someone adds one, this fails and the fixture above stops representing
        the real route.
        """
        import inspect

        from open_webui.utils import chat as chat_mod

        src = inspect.getsource(chat_mod)
        marker = "if 'done' in data and data['done']:"
        self.assertIn(marker, src)
        window = src[src.index(marker):src.index(marker) + 400]
        self.assertNotIn("[DONE]", window)


if __name__ == "__main__":
    unittest.main()

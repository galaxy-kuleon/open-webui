"""Behavioural probe: hermes_agent.pipe() translates hermes.memory.recalled SSE events.

Feeds a mock SSE stream containing ``event: hermes.memory.recalled`` frames and
verifies that ``__event_emitter__`` is called with the expected payload shape.
Also asserts that an SSE stream without the event produces no recall emission.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# SSE stream factory helpers
# ---------------------------------------------------------------------------


def _make_fake_httpx_client_with_sse(lines: list[str]):
    """Return a mock httpx.AsyncClient whose stream yields ``lines`` then terminates."""

    async def _fake_aiter_lines():
        for line in lines:
            yield line

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.aiter_lines = _fake_aiter_lines

    fake_stream_cm = MagicMock()
    fake_stream_cm.__aenter__ = AsyncMock(return_value=fake_response)
    fake_stream_cm.__aexit__ = AsyncMock(return_value=False)

    fake_client = MagicMock()
    fake_client.stream = MagicMock(return_value=fake_stream_cm)

    fake_client_cm = MagicMock()
    fake_client_cm.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client_cm.__aexit__ = AsyncMock(return_value=False)

    return fake_client_cm


def _memory_recall_sse_lines(
    provider: str = "holographic",
    context_preview: str = "user mentioned their favourite colour is blue",
    context_token_estimate: int = 11,
) -> list[str]:
    import json

    payload = json.dumps(
        {
            "provider": provider,
            "context_preview": context_preview,
            "context_token_estimate": context_token_estimate,
        }
    )
    return [
        "event: hermes.memory.recalled",
        f"data: {payload}",
        "",  # SSE event boundary
        "data: [DONE]",
    ]


def _no_recall_sse_lines() -> list[str]:
    """SSE stream with only a content chunk and DONE — no memory recall event."""
    import json

    chunk = json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "Hi!"}, "finish_reason": None}],
        }
    )
    return [f"data: {chunk}", "", "data: [DONE]"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_recall_event_triggers_emitter():
    """hermes.memory.recalled SSE event → __event_emitter__ called with hermes_memory_recall action."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    emitter_calls: list[dict] = []

    async def capture_emitter(event: dict):
        emitter_calls.append(event)

    body = {"messages": [{"role": "user", "content": "what colour?"}], "model": "hermes"}

    with patch(
        "httpx.AsyncClient",
        return_value=_make_fake_httpx_client_with_sse(_memory_recall_sse_lines()),
    ):
        async for _ in pipe.pipe(
            body=body,
            __user__=None,
            __event_emitter__=capture_emitter,
        ):
            pass

    recall_calls = [
        c for c in emitter_calls if c.get("data", {}).get("action") == "hermes_memory_recall"
    ]
    assert len(recall_calls) >= 1, (
        f"Expected at least one hermes_memory_recall emitter call. Got calls: {emitter_calls}"
    )

    assert recall_calls[0]["type"] == "status", "Event type must be 'status'"
    data = recall_calls[0]["data"]
    assert data["provider"] == "holographic"
    assert "context_preview" in data
    assert data["context_preview"] == "user mentioned their favourite colour is blue"
    assert "context_token_estimate" in data
    assert data["context_token_estimate"] == 11
    assert data["done"] is False


@pytest.mark.asyncio
async def test_no_memory_recall_event_when_absent_from_stream():
    """SSE stream with no hermes.memory.recalled event → no hermes_memory_recall emitter call."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    emitter_calls: list[dict] = []

    async def capture_emitter(event: dict):
        emitter_calls.append(event)

    body = {"messages": [{"role": "user", "content": "hello"}], "model": "hermes"}

    with patch(
        "httpx.AsyncClient",
        return_value=_make_fake_httpx_client_with_sse(_no_recall_sse_lines()),
    ):
        async for _ in pipe.pipe(
            body=body,
            __user__=None,
            __event_emitter__=capture_emitter,
        ):
            pass

    recall_calls = [
        c for c in emitter_calls if c.get("data", {}).get("action") == "hermes_memory_recall"
    ]
    assert len(recall_calls) == 0, (
        f"hermes_memory_recall must NOT be called when SSE stream has no recall event. Got: {recall_calls}"
    )

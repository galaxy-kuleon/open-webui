"""Behavioural test for openwebui pipe → hermes.continuation.suggested
translation (W4 T1).

The pipe parses mixed SSE streams from hermes. When it sees the new
event type ``event: hermes.continuation.suggested``, it MUST dispatch
a ``__event_emitter__`` status event with ``action="hermes_continuation"``
and the payload fields (task_summary, confidence, last_session_age_hours).

A frontend ContinueCard is deferred (W4b). This test verifies ONLY the
plumbing contract: the action name and payload shape are stable.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_webui.pipes.hermes_agent import Pipe


class _FakeSSEStream:
    """async-iter over a canned list of SSE lines to mimic httpx.aiter_lines."""

    def __init__(self, lines: List[str]):
        self._lines = lines

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


def _make_fake_response(lines: List[str]):
    resp = MagicMock()
    resp.status_code = 200
    resp.aiter_lines = lambda: _FakeSSEStream(lines)
    return resp


def _make_pipe_with_mocked_stream(sse_lines: List[str]):
    """Return (pipe, captured_emitter_calls) with httpx patched to yield sse_lines."""
    pipe = Pipe()
    pipe.valves.hermes_api_url = 'http://test-hermes:8642'

    fake_response = _make_fake_response(sse_lines)

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__.return_value = fake_response
    mock_stream_ctx.__aexit__.return_value = None

    mock_client_instance = MagicMock()
    mock_client_instance.stream = MagicMock(return_value=mock_stream_ctx)

    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__.return_value = mock_client_instance
    mock_client_ctx.__aexit__.return_value = None

    return pipe, mock_client_ctx


async def _drain_pipe_collect_events(pipe: Pipe, mock_client_ctx):
    """Run the pipe with a mocked httpx and return emitter events captured."""
    events: List[Dict[str, Any]] = []

    async def emitter(ev):
        events.append(ev)

    with patch('httpx.AsyncClient', return_value=mock_client_ctx):
        async for _ in pipe.pipe(
            body={'model': 'default', 'messages': [{'role': 'user', 'content': 'hi'}]},
            __event_emitter__=emitter,
            __user__=None,
            __chat_id__=None,
            __files__=None,
            __metadata__=None,
        ):
            pass
    return events


@pytest.mark.asyncio
async def test_continuation_event_triggers_emitter():
    """SSE stream containing hermes.continuation.suggested → emitter called with
    action=hermes_continuation and task_summary payload."""
    payload = {
        'task_summary': 'You were porting skip_rag.py to the new ABC.',
        'confidence': 'low',
        'last_session_age_hours': 26,
    }
    sse_lines = [
        'event: hermes.continuation.suggested',
        f'data: {json.dumps(payload)}',
        '',
        'data: [DONE]',
    ]
    pipe, mock_client_ctx = _make_pipe_with_mocked_stream(sse_lines)
    events = await _drain_pipe_collect_events(pipe, mock_client_ctx)

    recall_events = [
        ev for ev in events if ev.get('data', {}).get('action') == 'hermes_continuation'
    ]
    assert recall_events, f'no hermes_continuation emitter call found; got {events}'
    assert len(recall_events) == 1
    data = recall_events[0]['data']
    assert data['task_summary'] == payload['task_summary']
    assert data['confidence'] == 'low'
    assert data['last_session_age_hours'] == 26


@pytest.mark.asyncio
async def test_no_continuation_when_absent_from_stream():
    """SSE stream without the continuation event → no hermes_continuation
    emitter call."""
    sse_lines = [
        'data: {"choices":[{"delta":{"content":"hello"}}]}',
        '',
        'data: [DONE]',
    ]
    pipe, mock_client_ctx = _make_pipe_with_mocked_stream(sse_lines)
    events = await _drain_pipe_collect_events(pipe, mock_client_ctx)

    cont_events = [
        ev for ev in events if ev.get('data', {}).get('action') == 'hermes_continuation'
    ]
    assert cont_events == [], (
        'hermes_continuation must not be emitted when event absent; '
        f'got {events}'
    )

"""Unit tests for recalled_facts forwarding in _emit_memory_recall.

Verifies that:
  1. When hermes SSE payload includes recalled_facts, they are forwarded
     on the status event with content_preview truncated to 200 chars.
  2. When hermes SSE payload omits recalled_facts, the status event still
     carries recalled_facts: [] (never missing).
  3. Content_preview longer than 200 chars is truncated on the OWUI side.
  4. Malformed fact entries (non-dict items) are silently skipped.
  5. score=None and score=float are both handled; non-numeric score becomes None.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# SSE stream helpers (reuse the same pattern as test_hermes_memory_recall.py)
# ---------------------------------------------------------------------------


def _make_fake_httpx_client_with_sse(lines: list[str]):
    """Return a mock httpx.AsyncClient whose stream yields lines then stops."""

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


def _sse_with_recall(payload_extra: dict) -> list[str]:
    """Build SSE lines for hermes.memory.recalled with caller-supplied extra fields."""
    import json

    base = {
        'provider': 'holographic',
        'context_preview': 'some context',
        'context_token_estimate': 5,
    }
    base.update(payload_extra)
    data = json.dumps(base)
    return [
        'event: hermes.memory.recalled',
        f'data: {data}',
        '',
        'data: [DONE]',
    ]


def _drain_recall_calls(emitter_calls: list[dict]) -> list[dict]:
    return [c for c in emitter_calls if c.get('data', {}).get('action') == 'hermes_memory_recall']


# ---------------------------------------------------------------------------
# Direct unit tests on _emit_memory_recall (no pipe overhead)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recalled_facts_forwarded_when_present():
    """When payload has recalled_facts, they appear on the emitted event."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    payload = {
        'provider': 'holographic',
        'context_preview': 'some preview',
        'context_token_estimate': 10,
        'recalled_facts': [
            {'id': '42', 'content_preview': 'user likes blue', 'score': 0.92},
            {'id': '7', 'content_preview': 'user is a developer', 'score': None},
        ],
    }

    await Pipe._emit_memory_recall(capture, payload)

    assert len(emitted) == 1
    data = emitted[0]['data']
    assert data['action'] == 'hermes_memory_recall'
    facts = data['recalled_facts']
    assert isinstance(facts, list)
    assert len(facts) == 2

    assert facts[0]['id'] == '42'
    assert facts[0]['content_preview'] == 'user likes blue'
    assert facts[0]['score'] == pytest.approx(0.92)

    assert facts[1]['id'] == '7'
    assert facts[1]['content_preview'] == 'user is a developer'
    assert facts[1]['score'] is None


@pytest.mark.asyncio
async def test_recalled_facts_empty_when_key_absent():
    """When payload omits recalled_facts entirely, emitted event has recalled_facts: []."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    payload = {
        'provider': 'holographic',
        'context_preview': 'some context',
        'context_token_estimate': 5,
        # deliberately NO recalled_facts key
    }

    await Pipe._emit_memory_recall(capture, payload)

    assert len(emitted) == 1
    data = emitted[0]['data']
    assert 'recalled_facts' in data, 'recalled_facts key must always be present on emitted event'
    assert data['recalled_facts'] == [], 'recalled_facts must be [] when absent from upstream payload'


@pytest.mark.asyncio
async def test_content_preview_truncated_to_200_chars():
    """content_preview longer than 200 chars is truncated on the OWUI side."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    long_preview = 'x' * 350  # well over the 200-char limit
    payload = {
        'provider': 'holographic',
        'recalled_facts': [
            {'id': '1', 'content_preview': long_preview, 'score': 0.5},
        ],
    }

    await Pipe._emit_memory_recall(capture, payload)

    facts = emitted[0]['data']['recalled_facts']
    assert len(facts) == 1
    assert len(facts[0]['content_preview']) == 200, (
        f'content_preview must be truncated to 200 chars, got {len(facts[0]["content_preview"])}'
    )
    assert facts[0]['content_preview'] == 'x' * 200


@pytest.mark.asyncio
async def test_content_preview_exactly_200_not_truncated():
    """A content_preview of exactly 200 chars is preserved unchanged."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    exact_preview = 'a' * 200
    payload = {
        'provider': 'holographic',
        'recalled_facts': [
            {'id': '99', 'content_preview': exact_preview, 'score': 1.0},
        ],
    }

    await Pipe._emit_memory_recall(capture, payload)

    facts = emitted[0]['data']['recalled_facts']
    assert facts[0]['content_preview'] == exact_preview


@pytest.mark.asyncio
async def test_non_numeric_score_becomes_none():
    """A non-numeric score value is coerced to None rather than crashing."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    payload = {
        'provider': 'holographic',
        'recalled_facts': [
            {'id': '5', 'content_preview': 'some fact', 'score': 'not-a-number'},
        ],
    }

    await Pipe._emit_memory_recall(capture, payload)

    facts = emitted[0]['data']['recalled_facts']
    assert facts[0]['score'] is None


@pytest.mark.asyncio
async def test_malformed_non_dict_entries_are_skipped():
    """Non-dict items in recalled_facts list are silently dropped."""
    from open_webui.pipes.hermes_agent import Pipe

    emitted: list[dict] = []

    async def capture(event: dict):
        emitted.append(event)

    payload = {
        'provider': 'holographic',
        'recalled_facts': [
            'not a dict',
            42,
            None,
            {'id': '3', 'content_preview': 'valid fact', 'score': 0.7},
        ],
    }

    await Pipe._emit_memory_recall(capture, payload)

    facts = emitted[0]['data']['recalled_facts']
    assert len(facts) == 1, 'Only the valid dict entry should survive'
    assert facts[0]['id'] == '3'


@pytest.mark.asyncio
async def test_recalled_facts_forwarded_via_full_pipe():
    """Integration: hermes.memory.recalled SSE event → pipe emits recalled_facts on status."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    emitter_calls: list[dict] = []

    async def capture_emitter(event: dict):
        emitter_calls.append(event)

    sse_lines = _sse_with_recall(
        {
            'recalled_facts': [
                {'id': '10', 'content_preview': 'user prefers dark mode', 'score': 0.88},
            ]
        }
    )

    body = {'messages': [{'role': 'user', 'content': 'theme?'}], 'model': 'hermes'}

    with patch(
        'httpx.AsyncClient',
        return_value=_make_fake_httpx_client_with_sse(sse_lines),
    ):
        async for _ in pipe.pipe(
            body=body,
            __user__=None,
            __event_emitter__=capture_emitter,
        ):
            pass

    recall_calls = _drain_recall_calls(emitter_calls)
    assert len(recall_calls) >= 1
    facts = recall_calls[0]['data']['recalled_facts']
    assert len(facts) == 1
    assert facts[0]['id'] == '10'
    assert facts[0]['content_preview'] == 'user prefers dark mode'
    assert facts[0]['score'] == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_empty_recalled_facts_via_full_pipe():
    """Integration: when hermes SSE payload has no recalled_facts, emitted event has recalled_facts: []."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    emitter_calls: list[dict] = []

    async def capture_emitter(event: dict):
        emitter_calls.append(event)

    # No recalled_facts in the SSE payload
    sse_lines = _sse_with_recall({})

    body = {'messages': [{'role': 'user', 'content': 'hello'}], 'model': 'hermes'}

    with patch(
        'httpx.AsyncClient',
        return_value=_make_fake_httpx_client_with_sse(sse_lines),
    ):
        async for _ in pipe.pipe(
            body=body,
            __user__=None,
            __event_emitter__=capture_emitter,
        ):
            pass

    recall_calls = _drain_recall_calls(emitter_calls)
    assert len(recall_calls) >= 1
    data = recall_calls[0]['data']
    assert 'recalled_facts' in data
    assert data['recalled_facts'] == []

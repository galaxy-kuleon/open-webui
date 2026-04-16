"""Tests for Pipe._emit_tool_progress() — SSE event translation.

Covers:
  (a) Full payload → emitter receives correctly shaped status event
  (b) Missing 'label' field → tool_input is '' and description omits colon-suffix
  (c) Missing 'tool' field → defaults to 'unknown', description uses 'unknown'
  (d) Missing all fields → all defaults applied, no KeyError raised
  (e) None emitter → call is a no-op, no exception

The method is a static async method on Pipe; we call it directly without
constructing a full Pipe instance where possible (but Pipe() is cheap here).
"""

import pytest

from open_webui.pipes.hermes_agent import Pipe


# ---------------------------------------------------------------------------
# Helper: capture emitted events
# ---------------------------------------------------------------------------

def _make_emitter():
    """Return (emitter_fn, captured_list). emitter_fn is async."""
    captured = []

    async def _emitter(event):
        captured.append(event)

    return _emitter, captured


# ---------------------------------------------------------------------------
# (a) Full payload — nominal case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_tool_progress_full_payload():
    """Full payload must produce a status event with all fields mapped."""
    emitter, captured = _make_emitter()
    payload = {'tool': 'web_search', 'emoji': '🔍', 'label': 'Searching...'}

    await Pipe._emit_tool_progress(emitter, payload)

    assert len(captured) == 1
    event = captured[0]

    assert event['type'] == 'status'
    data = event['data']
    assert data['action'] == 'agent_skill'
    assert data['sub_action'] == 'tool_use'
    assert data['tool_name'] == 'web_search'
    assert data['tool_input'] == 'Searching...'
    assert data['description'] == '🔍 web_search: Searching...'
    assert data['done'] is False


# ---------------------------------------------------------------------------
# (b) Label absent — description collapses to emoji + tool only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_tool_progress_no_label():
    """When 'label' is absent, tool_input is '' and description has no colon suffix."""
    emitter, captured = _make_emitter()
    payload = {'tool': 'code_exec', 'emoji': '⚙️'}

    await Pipe._emit_tool_progress(emitter, payload)

    assert len(captured) == 1
    data = captured[0]['data']
    assert data['tool_name'] == 'code_exec'
    assert data['tool_input'] == ''
    # No label means no ': ...' suffix
    description = data['description']
    assert 'code_exec' in description
    assert ':' not in description or description.endswith('code_exec')


# ---------------------------------------------------------------------------
# (c) Tool field absent — defaults to 'unknown'
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_tool_progress_missing_tool_defaults_unknown():
    """When 'tool' key is absent, tool_name must be 'unknown'."""
    emitter, captured = _make_emitter()
    payload = {'emoji': '?', 'label': 'working'}

    await Pipe._emit_tool_progress(emitter, payload)

    assert len(captured) == 1
    data = captured[0]['data']
    assert data['tool_name'] == 'unknown'
    assert 'unknown' in data['description']


# ---------------------------------------------------------------------------
# (d) Empty payload — all defaults, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_tool_progress_empty_payload_no_error():
    """Empty payload must not raise; defaults kick in (tool='unknown', etc.)."""
    emitter, captured = _make_emitter()

    await Pipe._emit_tool_progress(emitter, {})

    assert len(captured) == 1
    data = captured[0]['data']
    assert data['tool_name'] == 'unknown'
    assert data['tool_input'] == ''
    assert data['done'] is False


# ---------------------------------------------------------------------------
# (e) None emitter — no-op, no exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_tool_progress_none_emitter_is_noop():
    """Passing None as emitter must silently return without raising."""
    # Should not raise
    await Pipe._emit_tool_progress(None, {'tool': 'web_search', 'emoji': '🔍', 'label': 'hi'})

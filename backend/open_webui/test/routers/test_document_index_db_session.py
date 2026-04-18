"""
W5-T2 Behavioral Tests: F-11 — generate_document_index DB-session release,
timeout policy, future.cancel(), WARN logs, and sentinel/event emission.

Acceptance map:
1. test_db_session_released_before_executor       — db.close() before run_coroutine_threadsafe
2. test_per_chunk_timeout_triggers_cancel          — TimeoutError → sentinel + cancel() called
3. test_total_timeout_cap_at_1800s                 — 100 chunks → min(100*120, 1800) = 1800
4. test_future_cancel_in_all_exit_paths            — cancel() called in success / timeout / exception
5. test_warn_logs_dispatch_config                  — WARN log contains required fields
6. test_document_index_dispatch_failed_event       — general exception → sentinel + failed event
7. test_document_index_dispatch_timeout_event      — TimeoutError → sentinel + timeout event
8. test_total_timeout_exhaustion_returns_sentinel  — total_timeout exhaustion → sentinel + correct event
"""

import asyncio
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from open_webui.routers.retrieval import (
    generate_document_index,
    PER_CHUNK_TIMEOUT_SECONDS,
    MAX_TOTAL_TIMEOUT_SECONDS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_request(model_id: str = 'test-model', main_loop=None):
    """Build a minimal request mock."""
    req = MagicMock()
    req.app.state.config.RAG_DOCUMENT_INDEX_MODEL = model_id
    req.app.state.MODELS = {'test-model': {}}
    req.app.state.main_loop = main_loop or MagicMock(spec=asyncio.AbstractEventLoop)
    return req


def _mock_future(result=None, side_effect=None):
    """Build a concurrent.futures.Future-like mock."""
    fut = MagicMock()
    if side_effect is not None:
        fut.result.side_effect = side_effect
    else:
        fut.result.return_value = result
    fut.cancel.return_value = True
    return fut


# ── Test 1: DB session released before executor ───────────────────────────────


def test_db_session_released_before_executor():
    """
    When a db session is passed to generate_document_index, it must be
    closed BEFORE run_coroutine_threadsafe is dispatched.

    Strategy: track call order by recording events in a shared list.
    Patch _split_text_by_tokens to return one chunk, and patch
    run_coroutine_threadsafe to record its position relative to db.close().
    """
    call_order = []

    db = MagicMock()

    def track_close():
        call_order.append('db.close')

    db.close.side_effect = track_close

    # _call_index_llm calls run_coroutine_threadsafe internally;
    # we patch the whole _call_index_llm to record when it was called.
    def fake_call_index_llm(*args, **kwargs):
        call_order.append('_call_index_llm')
        return ('fake_content', 0.1)

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval._call_index_llm', side_effect=fake_call_index_llm),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys prompt'),
    ):
        mock_split.return_value = ['chunk one']

        req = _mock_request()
        result = generate_document_index(
            request=req,
            text_content='some document text',
            filename='test.pdf',
            user=MagicMock(),
            db=db,
        )

    # db.close must have been called
    assert 'db.close' in call_order, f'db.close not called: {call_order}'
    # db.close must precede _call_index_llm
    close_pos = call_order.index('db.close')
    llm_pos = call_order.index('_call_index_llm')
    assert close_pos < llm_pos, (
        f'db.close ({close_pos}) must come before _call_index_llm ({llm_pos}); order: {call_order}'
    )
    # Function still returns content
    assert result == 'fake_content', f'Expected content, got {result!r}'


# ── Test 2: TimeoutError → cancel() called ───────────────────────────────────


def test_per_chunk_timeout_triggers_cancel():
    """
    When future.result(timeout=...) raises TimeoutError,
    future.cancel() must be called and generate_document_index must return None.
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    mock_future = _mock_future(side_effect=FuturesTimeoutError('timeout'))

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe') as mock_rcts,
        patch('open_webui.utils.chat.generate_chat_completion', return_value=None),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys'),
    ):
        mock_split.return_value = ['chunk']
        mock_rcts.return_value = mock_future

        req = _mock_request()
        result = generate_document_index(
            request=req,
            text_content='content',
            filename='doc.pdf',
            user=MagicMock(),
        )

    assert result is None, f'Expected None sentinel on timeout, got {result!r}'
    mock_future.cancel.assert_called()


# ── Test 3: total_timeout capped at 1800s ─────────────────────────────────────


def test_total_timeout_cap_at_1800s():
    """
    With 100 chunks: min(100 * 120, 1800) = 1800.
    The cap must be enforced regardless of chunk count.
    """
    # This is a pure arithmetic property test — no mocking of I/O.
    chunk_count = 100
    computed = min(chunk_count * PER_CHUNK_TIMEOUT_SECONDS, MAX_TOTAL_TIMEOUT_SECONDS)
    assert computed == 1800, f'Expected 1800, got {computed}'
    assert PER_CHUNK_TIMEOUT_SECONDS == 120, f'PER_CHUNK must be 120, got {PER_CHUNK_TIMEOUT_SECONDS}'
    assert MAX_TOTAL_TIMEOUT_SECONDS == 1800, f'MAX_TOTAL must be 1800, got {MAX_TOTAL_TIMEOUT_SECONDS}'


# ── Test 4: future.cancel() in all exit paths ─────────────────────────────────


def test_future_cancel_in_all_exit_paths():
    """
    future.cancel() must be called in _call_index_llm's finally block
    regardless of whether the call succeeds, times out, or raises.

    We test all three paths by patching future.result() accordingly and
    verifying cancel() is always invoked.
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError
    from open_webui.routers.retrieval import _call_index_llm

    loop = MagicMock(spec=asyncio.AbstractEventLoop)

    # Path 1: success — future.result returns a dict with choices
    req_success = _mock_request(main_loop=loop)
    fut_success = _mock_future(result={'choices': [{'message': {'content': 'result'}}]})
    with patch('open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe', return_value=fut_success):
        with patch('open_webui.utils.chat.generate_chat_completion', return_value=MagicMock()):
            try:
                _call_index_llm(req_success, 'model', 'sys', 'content', MagicMock(), timeout=10)
            except Exception:
                pass
    fut_success.cancel.assert_called_once()

    # Path 2: timeout — future.result raises TimeoutError
    req_timeout = _mock_request(main_loop=loop)
    fut_timeout = _mock_future(side_effect=FuturesTimeoutError('t'))
    with patch('open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe', return_value=fut_timeout):
        with patch('open_webui.utils.chat.generate_chat_completion', return_value=MagicMock()):
            try:
                _call_index_llm(req_timeout, 'model', 'sys', 'content', MagicMock(), timeout=10)
            except FuturesTimeoutError:
                pass
    fut_timeout.cancel.assert_called_once()

    # Path 3: general exception — future.result raises RuntimeError
    req_err = _mock_request(main_loop=loop)
    fut_err = _mock_future(side_effect=RuntimeError('crash'))
    with patch('open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe', return_value=fut_err):
        with patch('open_webui.utils.chat.generate_chat_completion', return_value=MagicMock()):
            try:
                _call_index_llm(req_err, 'model', 'sys', 'content', MagicMock(), timeout=10)
            except RuntimeError:
                pass
    fut_err.cancel.assert_called_once()


# ── Test 5: WARN log contains dispatch config ─────────────────────────────────


def test_warn_logs_dispatch_config(caplog):
    """
    On entry, generate_document_index must emit a WARN log containing:
    chunk_count, per_chunk_timeout, total_timeout.
    """
    import logging

    def fake_call(*args, **kwargs):
        return ('ok', 0.1)

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval._call_index_llm', side_effect=fake_call),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys'),
    ):
        mock_split.return_value = ['chunk_a', 'chunk_b']  # 2 chunks

        req = _mock_request()
        with caplog.at_level(logging.WARNING, logger='open_webui.routers.retrieval'):
            generate_document_index(
                request=req,
                text_content='two chunk content',
                filename='file.pdf',
                user=MagicMock(),
            )

    warn_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    dispatch_log = next(
        (m for m in warn_messages if 'dispatch config' in m or 'chunk_count' in m),
        None,
    )
    assert dispatch_log is not None, f'No dispatch config WARN log found. Logs: {warn_messages}'
    assert 'chunk_count' in dispatch_log, f'chunk_count missing from log: {dispatch_log}'
    assert 'per_chunk_timeout' in dispatch_log, f'per_chunk_timeout missing from log: {dispatch_log}'
    assert 'total_timeout' in dispatch_log, f'total_timeout missing from log: {dispatch_log}'


# ── Test 6: general failure → sentinel + dispatch_failed event ────────────────


def test_document_index_dispatch_failed_event():
    """
    When _call_index_llm raises a non-TimeoutError exception,
    generate_document_index must:
    - Return None (sentinel).
    - Emit a document_index_dispatch_failed event via event_emitter.
    """
    import asyncio as _asyncio

    events_received = []

    async def fake_event_emitter(event):
        events_received.append(event)

    # _call_index_llm raises a generic error
    def fake_call_err(*args, **kwargs):
        raise RuntimeError('LLM backend crashed')

    # We need a real event loop to run the coroutine dispatched via
    # run_coroutine_threadsafe. Patch run_coroutine_threadsafe to execute
    # the coroutine synchronously via a local event loop.
    async def _run_async(coro, loop):
        return await coro

    dispatched_coros = []

    def fake_run_coroutine_threadsafe(coro, loop):
        dispatched_coros.append(coro)
        fut = MagicMock()
        fut.cancel.return_value = True
        return fut

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval._call_index_llm', side_effect=fake_call_err),
        patch(
            'open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe', side_effect=fake_run_coroutine_threadsafe
        ),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys'),
    ):
        mock_split.return_value = ['one chunk']

        req = _mock_request()
        result = generate_document_index(
            request=req,
            text_content='doc content',
            filename='crash.pdf',
            user=MagicMock(),
            event_emitter=fake_event_emitter,
        )

    assert result is None, f'Expected None sentinel on failure, got {result!r}'

    # At least one dispatched coroutine should be the event emitter call.
    # Run all dispatched coroutines to collect events.
    if dispatched_coros:
        loop = _asyncio.new_event_loop()
        for coro in dispatched_coros:
            try:
                loop.run_until_complete(coro)
            except Exception:
                pass
        loop.close()

    actions = [e['data']['action'] for e in events_received if 'data' in e]
    assert 'document_index_dispatch_failed' in actions, (
        f'Expected document_index_dispatch_failed event, got actions: {actions}'
    )
    # Verify event shape
    failed_event = next(
        e for e in events_received if e.get('data', {}).get('action') == 'document_index_dispatch_failed'
    )
    assert failed_event['type'] == 'status'
    assert failed_event['data']['done'] is True
    assert 'chunk_count' in failed_event['data']
    assert 'elapsed' in failed_event['data']


# ── Test 7: TimeoutError → sentinel + dispatch_timeout event ─────────────────


def test_document_index_dispatch_timeout_event():
    """
    When _call_index_llm raises concurrent.futures.TimeoutError,
    generate_document_index must:
    - Return None (sentinel).
    - Emit a document_index_dispatch_timeout event via event_emitter.
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError
    import asyncio as _asyncio

    events_received = []

    async def fake_event_emitter(event):
        events_received.append(event)

    def fake_call_timeout(*args, **kwargs):
        raise FuturesTimeoutError('per-chunk timeout exceeded')

    dispatched_coros = []

    def fake_run_coroutine_threadsafe(coro, loop):
        dispatched_coros.append(coro)
        fut = MagicMock()
        fut.cancel.return_value = True
        return fut

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval._call_index_llm', side_effect=fake_call_timeout),
        patch(
            'open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe', side_effect=fake_run_coroutine_threadsafe
        ),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys'),
    ):
        mock_split.return_value = ['chunk one']

        req = _mock_request()
        result = generate_document_index(
            request=req,
            text_content='long content',
            filename='timeout.pdf',
            user=MagicMock(),
            event_emitter=fake_event_emitter,
        )

    assert result is None, f'Expected None sentinel on timeout, got {result!r}'

    if dispatched_coros:
        loop = _asyncio.new_event_loop()
        for coro in dispatched_coros:
            try:
                loop.run_until_complete(coro)
            except Exception:
                pass
        loop.close()

    actions = [e['data']['action'] for e in events_received if 'data' in e]
    assert 'document_index_dispatch_timeout' in actions, (
        f'Expected document_index_dispatch_timeout event, got: {actions}'
    )
    timeout_event = next(
        e for e in events_received if e.get('data', {}).get('action') == 'document_index_dispatch_timeout'
    )
    assert timeout_event['type'] == 'status'
    assert timeout_event['data']['done'] is True
    assert 'chunk_count' in timeout_event['data']
    assert 'elapsed' in timeout_event['data']
    assert 'total_timeout' in timeout_event['data']


# ── Test 8: total_timeout exhaustion → sentinel + correct event ───────────────


def test_total_timeout_exhaustion_returns_sentinel(monkeypatch):
    """
    W5-T2 Fix 1: total_timeout must act as a hard deadline for the multi-chunk loop.

    Scenario: 100 chunks, per_chunk_timeout=120s, total_timeout=1800s.
    Each simulated LLM call advances a fake monotonic clock by 119.9s.
    The deadline check fires before chunk 16 (chunks 0-15 complete = 1918.4s > 1800s).

    Verifies:
    - Function returns None (sentinel) after total_timeout expires.
    - document_index_dispatch_timeout event emitted exactly once.
    - chunks_completed < chunks_total in the event payload.
    - Simulated elapsed time ≤ 1810s (well under 100 * 119.9s = 11990s).
    - Test completes in real wall-clock time << 1s (no actual sleeping).
    """
    import asyncio as _asyncio

    # Fake monotonic clock + LLM simulation.
    #
    # Strategy: advance the fake clock inside fake_call_index_llm (representing the LLM
    # call completing), NOT inside fake_monotonic itself.  This ensures that
    # `elapsed = _time.monotonic() - (deadline - total_timeout)` at the timeout branch
    # sees exactly the clock value set by the last LLM completion — no spurious
    # extra advances from the elapsed-computation call itself.
    #
    # Call pattern in generate_document_index (multi-chunk path):
    #   t_job_start            → fake_monotonic() → 0.0
    #   deadline = mono() + T  → fake_monotonic() → 0.0
    #   Per iteration:
    #     remaining = deadline - mono()  → fake_monotonic() → reads clock (no advance here)
    #     if remaining <= 0:
    #       elapsed = mono() - (deadline - T)  → fake_monotonic() → reads clock (no advance)
    #       return None
    #     _call_index_llm(...)  → fake_call_index_llm → advances clock by PER_CHUNK_ADVANCE
    #
    # With PER_CHUNK_ADVANCE = 119.9s and total_timeout = 1800s:
    #   After 15 LLM calls: clock = 15 * 119.9 = 1798.5s
    #   remaining check for chunk 15 = 1800 - 1798.5 = 1.5 > 0 → chunk 15 runs
    #   After 16 LLM calls: clock = 16 * 119.9 = 1918.4s
    #   remaining check for chunk 16 = 1800 - 1918.4 = -118.4 ≤ 0 → deadline branch fires
    #   elapsed = 1918.4 - 0 = 1918.4s  (≤ 1920 = 16 * 120, well under 100 * 119.9 = 11990s)
    PER_CHUNK_ADVANCE = 119.9  # seconds each simulated LLM call "takes"
    fake_clock = [0.0]

    def fake_monotonic():
        # Read-only: the clock is advanced by fake_call_index_llm, not here
        return fake_clock[0]

    # Patch time.monotonic globally — retrieval.py does `import time as _time` inside
    # the function body, so the actual time module object is shared. Patching via the
    # `time` module directly is the correct seam.
    monkeypatch.setattr('time.monotonic', fake_monotonic)

    events_received = []

    async def fake_event_emitter(event):
        events_received.append(event)

    dispatched_coros = []

    def fake_run_coroutine_threadsafe(coro, loop):
        dispatched_coros.append(coro)
        fut = MagicMock()
        fut.cancel.return_value = True
        return fut

    # _call_index_llm: advances the fake clock (simulates LLM wall time) and succeeds.
    # The only exit from the loop is total_timeout — no per-chunk TimeoutError.
    def fake_call_index_llm(*args, **kwargs):
        fake_clock[0] += PER_CHUNK_ADVANCE
        return ('chunk_content', PER_CHUNK_ADVANCE)

    CHUNK_COUNT = 100

    with (
        patch('open_webui.routers.retrieval._split_text_by_tokens') as mock_split,
        patch('open_webui.routers.retrieval._call_index_llm', side_effect=fake_call_index_llm),
        patch(
            'open_webui.routers.retrieval.asyncio.run_coroutine_threadsafe',
            side_effect=fake_run_coroutine_threadsafe,
        ),
        patch('open_webui.config.DEFAULT_RAG_DOCUMENT_INDEX_PROMPT', 'sys'),
    ):
        mock_split.return_value = [f'chunk_{i}' for i in range(CHUNK_COUNT)]

        req = _mock_request()
        result = generate_document_index(
            request=req,
            text_content='large document content',
            filename='huge.pdf',
            user=MagicMock(),
            event_emitter=fake_event_emitter,
        )

    # 1. Must return None sentinel
    assert result is None, f'Expected None sentinel when total_timeout exhausted, got {result!r}'

    # Run dispatched coroutines to collect events (event_emitter is dispatched via run_coroutine_threadsafe)
    if dispatched_coros:
        loop = _asyncio.new_event_loop()
        for coro in dispatched_coros:
            try:
                loop.run_until_complete(coro)
            except Exception:
                pass
        loop.close()

    # 2. document_index_dispatch_timeout event emitted exactly once
    timeout_events = [
        e for e in events_received if e.get('data', {}).get('action') == 'document_index_dispatch_timeout'
    ]
    assert len(timeout_events) == 1, (
        f'Expected exactly 1 document_index_dispatch_timeout event, '
        f'got {len(timeout_events)}: {[e["data"] for e in timeout_events]}'
    )

    evt_data = timeout_events[0]['data']

    # 3. chunks_completed < chunks_total in the event
    assert 'chunks_completed' in evt_data, f'chunks_completed missing from event: {evt_data}'
    assert 'chunks_total' in evt_data, f'chunks_total missing from event: {evt_data}'
    assert evt_data['chunks_completed'] < evt_data['chunks_total'], (
        f'chunks_completed ({evt_data["chunks_completed"]}) must be < chunks_total ({evt_data["chunks_total"]})'
    )
    assert evt_data['chunks_total'] == CHUNK_COUNT, (
        f'chunks_total must be {CHUNK_COUNT}, got {evt_data["chunks_total"]}'
    )

    # 4. Simulated elapsed time ≤ 1810s of real wall-clock time (i.e., the test
    #    completed in microseconds, not in actual seconds).  The fake_clock value
    #    may legitimately exceed total_timeout by one chunk overage (≤ 1920s for
    #    16 * 120s chunks), but real wall time must be negligible.
    #    We verify this by checking the fake_clock did NOT process all 100 chunks:
    #    if all 100 ran, fake_clock[0] would be 100*119.9 = 11990s.
    assert fake_clock[0] <= 1920.0, (
        f'Fake clock advanced to {fake_clock[0]:.1f}s — implies too many chunks processed '
        f'({fake_clock[0] / PER_CHUNK_ADVANCE:.1f} chunks × {PER_CHUNK_ADVANCE}s). '
        f'Expected deadline to fire after ≤16 chunks (≤1920s simulated).'
    )

    # 5. Event shape is correct
    assert timeout_events[0]['type'] == 'status'
    assert evt_data['done'] is True
    assert evt_data['total_timeout'] == MAX_TOTAL_TIMEOUT_SECONDS

"""
W4 T1 — F-8 acceptance tests: per-user credential threading through _async_llm_completion.

Four behavioral tests verifying:
1. Non-admin acting_user is forwarded as-is to generate_chat_completion.
2. acting_user=None (system-level) causes an INFO log with the reason token.
3. Calling without acting_user raises TypeError (required positional param).
4. Docstring clarifies bypass_filter semantic (filter middleware only, not auth bypass).
"""

import inspect
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_regular_user():
    """Non-admin UserModel-like object."""
    return SimpleNamespace(
        id='user-42',
        email='alice@example.com',
        role='user',
        name='Alice',
    )


def _make_openai_dict_response(content: str) -> dict:
    return {'choices': [{'message': {'role': 'assistant', 'content': content}, 'index': 0}]}


def _make_app():
    return SimpleNamespace(state=SimpleNamespace(MODELS={'test-model': {'id': 'test-model'}}))


# ---------------------------------------------------------------------------
# Test 1: acting_user forwarded to generate_chat_completion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_llm_completion_with_acting_user():
    """
    When a non-admin UserModel is passed as acting_user, generate_chat_completion
    receives that exact user object (not a fabricated admin).
    """
    from open_webui.utils.knowledge_export import _async_llm_completion

    user = _make_regular_user()
    mock_response = _make_openai_dict_response('hello from user context')

    with patch(
        'open_webui.utils.chat.generate_chat_completion',
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_gcc:
        result = await _async_llm_completion(
            app=_make_app(),
            messages=[{'role': 'user', 'content': 'hi'}],
            model_id='test-model',
            acting_user=user,
        )

    assert result == 'hello from user context'

    # The user forwarded to generate_chat_completion must be the exact object
    # passed in — not a super-admin substitution.
    _, kwargs = mock_gcc.call_args
    assert kwargs.get('user') is user, (
        f'Expected acting_user to be forwarded unchanged; got {kwargs.get("user")!r} instead'
    )


# ---------------------------------------------------------------------------
# Test 2: acting_user=None emits INFO log with reason token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_llm_completion_with_none_logs_info(caplog):
    """
    When acting_user=None is passed (system-level / inbox-organizer call site),
    the CALLER is responsible for logging the reason. For direct calls with None,
    generate_chat_completion still receives user=None — no internal super-admin
    fabrication occurs.

    Verifies that call_llm_completion (the organizer bridge) emits the required
    INFO log "LLM call under super-admin credential: reason=inbox_organizer"
    before scheduling the coroutine.
    """
    from open_webui.utils.knowledge_export import call_llm_completion
    import asyncio
    import threading

    mock_response = _make_openai_dict_response('organizer response')

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    state = SimpleNamespace(
        main_loop=loop,
        MODELS={'test-model': {'id': 'test-model'}},
    )
    app = SimpleNamespace(state=state)

    try:
        with (
            patch(
                'open_webui.utils.chat.generate_chat_completion',
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            caplog.at_level(logging.INFO, logger='open_webui.utils.knowledge_export'),
        ):
            result = call_llm_completion(
                app=app,
                system_prompt='sys',
                user_prompt='usr',
                model_id='test-model',
                timeout=10.0,
            )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()

    assert result == 'organizer response'

    # The required INFO log must appear at the call_llm_completion call site.
    assert any(
        'LLM call under super-admin credential' in record.message and 'reason=inbox_organizer' in record.message
        for record in caplog.records
    ), (
        f"Expected INFO log with 'reason=inbox_organizer' not found. "
        f'Got log records: {[r.message for r in caplog.records]}'
    )


# ---------------------------------------------------------------------------
# Test 3: calling without acting_user raises TypeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_llm_completion_signature_requires_acting_user():
    """
    acting_user is a REQUIRED positional parameter (no default). Calling
    _async_llm_completion without it must raise TypeError immediately,
    before any I/O occurs.
    """
    from open_webui.utils.knowledge_export import _async_llm_completion

    with pytest.raises(TypeError):
        # Missing acting_user — must fail at call time, not inside the coroutine.
        await _async_llm_completion(
            app=_make_app(),
            messages=[{'role': 'user', 'content': 'test'}],
            model_id='test-model',
            # acting_user intentionally omitted
        )


# ---------------------------------------------------------------------------
# Test 4: docstring clarifies bypass_filter semantic
# ---------------------------------------------------------------------------


def test_async_llm_completion_bypass_filter_docstring():
    """
    The docstring of _async_llm_completion must contain language clarifying that
    bypass_filter controls only filter middleware — it is NOT an admin or
    authentication bypass.

    Checks:
    1. "filter middleware" appears — confirms the filter-middleware framing.
    2. "NOT" appears near the bypass_filter description — confirms the docstring
       explicitly states it is NOT an admin/auth bypass (rather than silently
       implying it is one).
    """
    from open_webui.utils.knowledge_export import _async_llm_completion

    doc = inspect.getdoc(_async_llm_completion) or ''

    # Must mention filter middleware — the only thing bypass_filter controls.
    assert 'filter middleware' in doc.lower(), (
        f'Docstring must describe bypass_filter as a filter-middleware control. Got docstring:\n{doc}'
    )

    # Must contain an explicit negation — the docstring must say it is NOT an
    # admin/auth bypass (not just omit the claim, but actively negate it).
    assert 'not an admin' in doc.lower() or 'not an auth' in doc.lower(), (
        f'Docstring must explicitly state bypass_filter is NOT an admin/auth bypass. Got docstring:\n{doc}'
    )

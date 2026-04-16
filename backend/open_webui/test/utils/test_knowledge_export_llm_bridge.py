"""
Behavioral tests for the async→sync LLM bridge and interface in knowledge_export.py.

Tests the bridge functions in isolation using mocks for the actual LLM call,
while verifying the real async→sync scheduling, response extraction, timeout,
error-handling semantics, and the simplified enqueue_organization / _organize_inbox
interface that reads configuration from app.state.config.
"""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_with_loop(loop: asyncio.AbstractEventLoop):
    """Build a minimal app-like object with state.main_loop set."""
    state = SimpleNamespace(main_loop=loop, MODELS={"test-model": {"id": "test-model"}})
    app = SimpleNamespace(state=state)
    return app


def _make_app_no_loop():
    """App with no main_loop — simulates startup race."""
    state = SimpleNamespace()
    app = SimpleNamespace(state=state)
    return app


def _make_openai_dict_response(content: str) -> dict:
    """Standard OpenAI-format dict response."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "index": 0}
        ]
    }


def _make_admin_user():
    """Minimal user-like object with admin role."""
    return SimpleNamespace(
        id="admin-test",
        email="admin@test.local",
        role="admin",
        name="Test Admin",
    )


# ---------------------------------------------------------------------------
# Test: call_llm_completion rejects when main_loop is absent
# ---------------------------------------------------------------------------

def test_call_llm_completion_no_loop():
    """When app.state.main_loop is not set, raise RuntimeError immediately."""
    from open_webui.utils.knowledge_export import call_llm_completion

    app = _make_app_no_loop()
    with pytest.raises(RuntimeError, match="Main event loop not available"):
        call_llm_completion(
            app=app,
            system_prompt="sys",
            user_prompt="usr",
            model_id="test-model",
        )


# ---------------------------------------------------------------------------
# Test: call_llm_completion rejects when main_loop is closed
# ---------------------------------------------------------------------------

def test_call_llm_completion_closed_loop():
    """When the event loop is closed, raise RuntimeError."""
    from open_webui.utils.knowledge_export import call_llm_completion

    loop = asyncio.new_event_loop()
    loop.close()  # close it immediately
    app = _make_app_with_loop(loop)
    with pytest.raises(RuntimeError, match="Main event loop not available"):
        call_llm_completion(
            app=app,
            system_prompt="sys",
            user_prompt="usr",
            model_id="test-model",
        )


# ---------------------------------------------------------------------------
# Test: _async_llm_completion extracts content from dict response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_dict_response():
    """When generate_chat_completion returns a dict, extract the content."""
    from open_webui.utils.knowledge_export import _async_llm_completion

    expected_content = '{"moves": [{"doc_id": "doc-0001", "destination": "test/dir", "reason": "test"}]}'
    mock_response = _make_openai_dict_response(expected_content)

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.utils.chat.generate_chat_completion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ),
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=_make_admin_user(),
        ),
    ):
        result = await _async_llm_completion(
            app=app,
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
            ],
            model_id="test-model",
        )
        assert result == expected_content


# ---------------------------------------------------------------------------
# Test: _async_llm_completion raises on empty content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_empty_content_raises():
    """When LLM returns empty content, raise RuntimeError."""
    from open_webui.utils.knowledge_export import _async_llm_completion

    mock_response = _make_openai_dict_response("")

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.utils.chat.generate_chat_completion",
            new_callable=AsyncMock,
            return_value=mock_response,
        ),
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=_make_admin_user(),
        ),
    ):
        with pytest.raises(RuntimeError, match="LLM returned empty content"):
            await _async_llm_completion(
                app=app,
                messages=[{"role": "user", "content": "test"}],
                model_id="test-model",
            )


# ---------------------------------------------------------------------------
# Test: _async_llm_completion raises when no admin user exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_no_admin_user():
    """When no admin user exists, raise RuntimeError."""
    from open_webui.utils.knowledge_export import _async_llm_completion

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=None,
        ),
        patch(
            "open_webui.models.users.Users.get_first_user",
            return_value=None,
        ),
    ):
        with pytest.raises(RuntimeError, match="No admin user available"):
            await _async_llm_completion(
                app=app,
                messages=[{"role": "user", "content": "test"}],
                model_id="test-model",
            )


# ---------------------------------------------------------------------------
# Test: full sync→async round-trip with a real event loop in another thread
# ---------------------------------------------------------------------------

def test_call_llm_completion_full_roundtrip():
    """
    End-to-end: call_llm_completion from a sync thread schedules work on a
    real event loop running in another thread, and returns the result.
    """
    from open_webui.utils.knowledge_export import call_llm_completion

    expected = '{"moves":[]}'
    mock_response = _make_openai_dict_response(expected)

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    app = _make_app_with_loop(loop)

    try:
        with (
            patch(
                "open_webui.utils.chat.generate_chat_completion",
                new_callable=AsyncMock,
                return_value=mock_response,
            ),
            patch(
                "open_webui.models.users.Users.get_super_admin_user",
                return_value=_make_admin_user(),
            ),
        ):
            result = call_llm_completion(
                app=app,
                system_prompt="You are a filing planner.",
                user_prompt="Organize these docs.",
                model_id="test-model",
                timeout=10.0,
            )
            assert result == expected
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()


# ---------------------------------------------------------------------------
# Test: call_llm_completion timeout behavior
# ---------------------------------------------------------------------------

def test_call_llm_completion_timeout():
    """When the LLM call takes too long, TimeoutError is raised."""
    from open_webui.utils.knowledge_export import call_llm_completion

    async def _slow_completion(*args, **kwargs):
        await asyncio.sleep(60)  # way longer than timeout
        return _make_openai_dict_response("too late")

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    app = _make_app_with_loop(loop)

    try:
        with (
            patch(
                "open_webui.utils.chat.generate_chat_completion",
                side_effect=_slow_completion,
            ),
            patch(
                "open_webui.models.users.Users.get_super_admin_user",
                return_value=_make_admin_user(),
            ),
        ):
            with pytest.raises(TimeoutError, match="timed out"):
                call_llm_completion(
                    app=app,
                    system_prompt="sys",
                    user_prompt="usr",
                    model_id="test-model",
                    timeout=0.5,  # very short timeout
                )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()


# ---------------------------------------------------------------------------
# Test: _async_llm_completion extracts content from JSONResponse-like body
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_json_response_body():
    """When response has .body attribute (JSONResponse), extract content."""
    from open_webui.utils.knowledge_export import _async_llm_completion

    expected = "planned response"
    response_data = _make_openai_dict_response(expected)

    # Simulate a JSONResponse (has .body, no .body_iterator, no 'choices' key directly)
    mock_json_response = SimpleNamespace(
        body=json.dumps(response_data).encode("utf-8"),
    )
    # Make sure it doesn't match as dict or body_iterator
    assert not isinstance(mock_json_response, dict)
    assert not hasattr(mock_json_response, "body_iterator")
    assert hasattr(mock_json_response, "body")

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.utils.chat.generate_chat_completion",
            new_callable=AsyncMock,
            return_value=mock_json_response,
        ),
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=_make_admin_user(),
        ),
    ):
        result = await _async_llm_completion(
            app=app,
            messages=[{"role": "user", "content": "test"}],
            model_id="test-model",
        )
        assert result == expected


# ---------------------------------------------------------------------------
# Test: call_llm_completion propagates LLM errors from async side
# ---------------------------------------------------------------------------

def test_call_llm_completion_propagates_llm_error():
    """If generate_chat_completion raises, the error propagates to the caller."""
    from open_webui.utils.knowledge_export import call_llm_completion

    async def _failing_completion(*args, **kwargs):
        raise ConnectionError("LLM backend unreachable")

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    app = _make_app_with_loop(loop)

    try:
        with (
            patch(
                "open_webui.utils.chat.generate_chat_completion",
                side_effect=_failing_completion,
            ),
            patch(
                "open_webui.models.users.Users.get_super_admin_user",
                return_value=_make_admin_user(),
            ),
        ):
            with pytest.raises(ConnectionError, match="LLM backend unreachable"):
                call_llm_completion(
                    app=app,
                    system_prompt="sys",
                    user_prompt="usr",
                    model_id="test-model",
                    timeout=10.0,
                )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)
        loop.close()


# ---------------------------------------------------------------------------
# Test: _async_llm_completion extracts content from StreamingResponse body_iterator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_streaming_response_body_iterator():
    """When response has body_iterator (StreamingResponse), drain and extract content.

    The body_iterator yields bytes chunks, each a JSON object in OpenAI format.
    The function should iterate all chunks and return the content from the last
    chunk that contains choices with content.
    Also verifies that response.background() is called when present.
    """
    from open_webui.utils.knowledge_export import _async_llm_completion

    expected = '{"moves": [{"doc_id": "doc-0001", "destination": "test/dir", "reason": "streaming"}]}'

    # Simulate a multi-chunk streaming response where only the last chunk has content.
    # This exercises the "overwrite content on each valid chunk" behavior.
    chunk_1 = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": ""}, "index": 0}]}
    ).encode("utf-8")
    chunk_2 = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": expected}, "index": 0}]}
    ).encode("utf-8")

    async def _fake_body_iterator():
        yield chunk_1
        yield chunk_2

    background_called = {"value": False}

    async def _fake_background():
        background_called["value"] = True

    mock_streaming_response = SimpleNamespace(
        body_iterator=_fake_body_iterator(),
        background=_fake_background,
    )
    # Verify it matches the body_iterator branch (not dict, not .body)
    assert not isinstance(mock_streaming_response, dict)
    assert hasattr(mock_streaming_response, "body_iterator")

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.utils.chat.generate_chat_completion",
            new_callable=AsyncMock,
            return_value=mock_streaming_response,
        ),
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=_make_admin_user(),
        ),
    ):
        result = await _async_llm_completion(
            app=app,
            messages=[{"role": "user", "content": "test streaming"}],
            model_id="test-model",
        )
        assert result == expected
        assert background_called["value"] is True, "background() was not called"


# ---------------------------------------------------------------------------
# Test: _async_llm_completion StreamingResponse with no background task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_llm_completion_streaming_response_no_background():
    """StreamingResponse without .background attribute still works."""
    from open_webui.utils.knowledge_export import _async_llm_completion

    expected = "streaming-no-bg"
    chunk = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": expected}, "index": 0}]}
    ).encode("utf-8")

    async def _fake_body_iterator():
        yield chunk

    mock_streaming_response = SimpleNamespace(
        body_iterator=_fake_body_iterator(),
        background=None,  # background is None => should not call it
    )

    app = SimpleNamespace(
        state=SimpleNamespace(MODELS={"test-model": {"id": "test-model"}})
    )

    with (
        patch(
            "open_webui.utils.chat.generate_chat_completion",
            new_callable=AsyncMock,
            return_value=mock_streaming_response,
        ),
        patch(
            "open_webui.models.users.Users.get_super_admin_user",
            return_value=_make_admin_user(),
        ),
    ):
        result = await _async_llm_completion(
            app=app,
            messages=[{"role": "user", "content": "test streaming no bg"}],
            model_id="test-model",
        )
        assert result == expected


# ---------------------------------------------------------------------------
# Helpers for enqueue_organization / _organize_inbox config-reading tests
# ---------------------------------------------------------------------------

def _make_app_with_config(**config_overrides):
    """Build an app-like object with state.config carrying RAG_KNOWLEDGE_* settings."""
    config = SimpleNamespace(
        RAG_KNOWLEDGE_EXPORT_DIR=config_overrides.get("export_dir", "/tmp/test-kb"),
        RAG_KNOWLEDGE_ORGANIZER_MODEL=config_overrides.get("model", "test-model"),
    )
    state = SimpleNamespace(config=config)
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# Test: _organize_inbox raises when app.state.config is absent
# ---------------------------------------------------------------------------

def test_organize_inbox_no_config():
    """When app.state.config is missing, raise RuntimeError."""
    from open_webui.utils.knowledge_export import _organize_inbox

    app = SimpleNamespace(state=SimpleNamespace())  # no config attr
    with pytest.raises(RuntimeError, match="app.state.config is not available"):
        _organize_inbox(app=app)


# ---------------------------------------------------------------------------
# Test: _organize_inbox skips when RAG_KNOWLEDGE_EXPORT_DIR is empty
# ---------------------------------------------------------------------------

def test_organize_inbox_empty_export_dir(caplog):
    """When RAG_KNOWLEDGE_EXPORT_DIR is empty, log warning and return (no error)."""
    from open_webui.utils.knowledge_export import _organize_inbox

    app = _make_app_with_config(export_dir="")
    import logging
    with caplog.at_level(logging.WARNING):
        _organize_inbox(app=app)
    assert "RAG_KNOWLEDGE_EXPORT_DIR is not configured" in caplog.text


# ---------------------------------------------------------------------------
# Test: _organize_inbox skips when RAG_KNOWLEDGE_ORGANIZER_MODEL is empty
# ---------------------------------------------------------------------------

def test_organize_inbox_empty_model(caplog):
    """When RAG_KNOWLEDGE_ORGANIZER_MODEL is empty, log warning and return (no error)."""
    from open_webui.utils.knowledge_export import _organize_inbox

    app = _make_app_with_config(export_dir="/tmp/test-kb", model="")
    import logging
    with caplog.at_level(logging.WARNING):
        _organize_inbox(app=app)
    assert "RAG_KNOWLEDGE_ORGANIZER_MODEL is not configured" in caplog.text


# ---------------------------------------------------------------------------
# Test: _organize_inbox skips when RAG_KNOWLEDGE_EXPORT_DIR is None
# ---------------------------------------------------------------------------

def test_organize_inbox_none_export_dir(caplog):
    """When RAG_KNOWLEDGE_EXPORT_DIR is None (falsy), log warning and return."""
    from open_webui.utils.knowledge_export import _organize_inbox

    app = _make_app_with_config(export_dir=None)
    import logging
    with caplog.at_level(logging.WARNING):
        _organize_inbox(app=app)
    assert "RAG_KNOWLEDGE_EXPORT_DIR is not configured" in caplog.text


# ---------------------------------------------------------------------------
# Test: _organize_inbox returns early when inbox dir doesn't exist
# ---------------------------------------------------------------------------

def test_organize_inbox_no_inbox_dir(tmp_path):
    """When the inbox subdirectory doesn't exist, return silently (no error)."""
    from open_webui.utils.knowledge_export import _organize_inbox

    # export_dir exists but has no "inbox" subdirectory
    app = _make_app_with_config(export_dir=str(tmp_path), model="test-model")
    # Should return without error
    _organize_inbox(app=app)


# ---------------------------------------------------------------------------
# Test: _organize_inbox returns early when inbox is empty
# ---------------------------------------------------------------------------

def test_organize_inbox_empty_inbox(tmp_path, caplog):
    """When inbox exists but has no .md files, log and return."""
    from open_webui.utils.knowledge_export import _organize_inbox

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    app = _make_app_with_config(export_dir=str(tmp_path), model="test-model")
    import logging
    with caplog.at_level(logging.INFO):
        _organize_inbox(app=app)
    assert "inbox is empty" in caplog.text


# ---------------------------------------------------------------------------
# Test: enqueue_organization takes only app (interface test)
# ---------------------------------------------------------------------------

def test_enqueue_organization_interface():
    """enqueue_organization(app) accepts a single argument and enqueues a job."""
    from open_webui.utils.knowledge_export import (
        enqueue_organization,
        _org_queue,
    )

    # Drain the queue first to avoid interference from other tests
    while not _org_queue.empty():
        try:
            _org_queue.get_nowait()
            _org_queue.task_done()
        except Exception:
            break

    app = _make_app_with_config()

    # Patch _ensure_worker_running to prevent actually starting a thread
    with patch("open_webui.utils.knowledge_export._ensure_worker_running"):
        enqueue_organization(app=app)

    assert not _org_queue.empty()
    job = _org_queue.get_nowait()
    _org_queue.task_done()
    assert job["app"] is app
    assert "enqueued_at" in job
    # Old params should NOT be in the job
    assert "export_dir" not in job
    assert "model" not in job


# ---------------------------------------------------------------------------
# Test: enqueue_organization deduplication still works
# ---------------------------------------------------------------------------

def test_enqueue_organization_dedup():
    """When a job is already queued, a second call is skipped."""
    from open_webui.utils.knowledge_export import (
        enqueue_organization,
        _org_queue,
    )

    # Drain queue
    while not _org_queue.empty():
        try:
            _org_queue.get_nowait()
            _org_queue.task_done()
        except Exception:
            break

    app = _make_app_with_config()

    with patch("open_webui.utils.knowledge_export._ensure_worker_running"):
        enqueue_organization(app=app)
        enqueue_organization(app=app)  # should be skipped

    # Only one job should be in the queue
    count = 0
    while not _org_queue.empty():
        _org_queue.get_nowait()
        _org_queue.task_done()
        count += 1
    assert count == 1, f"Expected 1 job in queue, got {count}"

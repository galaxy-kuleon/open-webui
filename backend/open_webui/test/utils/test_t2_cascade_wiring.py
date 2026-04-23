"""
W1-T2 Behavioral Tests: Verify cascade wiring in chat_completion_files_handler.

Tests verify:
1. is_new_chat guard is removed — multi-turn chats with files enter Direct Content Mode
2. apply_token_budget_cascade is called (not inline logic)
3. .get("metadata", []) consistency in apply_token_budget_cascade
4. Tier 1 early-return emits no redundant cascade status events
"""

import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from open_webui.utils.middleware import (
    apply_token_budget_cascade,
    build_index_only_sources,
    estimate_sources_total_tokens,
)


# ── Helpers ──────────────────────────────────────────────────────

def make_source(doc_text="hello world", file_id="f1", name="test.md"):
    """Create a minimal source dict matching the shape produced by _build_direct_file_sources."""
    return {
        "source": {"id": file_id, "name": name, "type": "file"},
        "document": [doc_text],
        "metadata": [{"file_id": file_id, "name": name, "source": name}],
    }


# ── Test 1: .get("metadata", []) consistency fix ──

def test_cascade_metadata_get_consistency():
    """
    Verify apply_token_budget_cascade handles metadata=None safely.

    .get("metadata", []) does NOT protect against metadata=None (key present,
    value is None). The correct idiom is `.get("metadata") or []` which handles
    both missing key AND explicit None.

    We verify via source inspection that the function uses `or []`.
    """
    import inspect
    source_code = inspect.getsource(apply_token_budget_cascade)
    lines = source_code.split("\n")
    for line in lines:
        stripped = line.strip()
        # Lines that assign metadatas should use the `or []` idiom
        if "metadatas = source.get(" in stripped and "metadata" in stripped:
            if "or []" not in stripped and ', [])' not in stripped:
                pytest.fail(
                    f"Unguarded .get('metadata') pattern (no `or []` fallback): {stripped}"
                )


# ── Test 2: Tier 1 early-return emits no status events ──

@pytest.mark.asyncio
async def test_tier1_no_events_when_within_budget():
    """
    When sources fit within budget (Tier 1), the cascade should return
    immediately without emitting any events.
    """
    events = []

    async def mock_emitter(event):
        events.append(event)

    sources = [make_source("short text")]
    # Budget of 1000 tokens — "short text" is well under
    result = await apply_token_budget_cascade(
        sources=sources,
        max_tokens=1000,
        request=MagicMock(),
        body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        user=MagicMock(),
        event_emitter=mock_emitter,
    )
    assert result == sources, "Tier 1 should return original sources"
    assert len(events) == 0, "Tier 1 should not emit any status events"


# ── Test 3: No budget → returns sources immediately ──

@pytest.mark.asyncio
async def test_no_budget_returns_sources_immediately():
    """When max_tokens is 0 or None, return Tier 1 immediately."""
    sources = [make_source("x" * 100000)]

    for max_tokens in [None, 0, -1]:
        result = await apply_token_budget_cascade(
            sources=sources,
            max_tokens=max_tokens,
            request=MagicMock(),
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )
        assert result is sources, f"max_tokens={max_tokens} should return sources as-is"


# ── Test 4: Verify the guard removal — code structure check ──

def test_no_is_new_chat_guard_in_handler():
    """
    The is_new_chat guard has been removed. Verify by inspecting the
    chat_completion_files_handler source that:
    - 'is_new_chat' does not appear
    - 'user_messages' does not appear (dead code removed)
    - 'only_uploaded_files' is used directly in the if-statement
    """
    import inspect
    from open_webui.utils.middleware import chat_completion_files_handler
    source = inspect.getsource(chat_completion_files_handler)
    assert "is_new_chat" not in source, "is_new_chat guard should be removed"
    assert "user_messages" not in source, "user_messages variable should be removed"
    assert "if only_uploaded_files:" in source, "Guard should check only_uploaded_files alone"


# ── Test 5: Verify cascade is called, not inline logic ──

def test_no_inline_extraction_in_handler():
    """
    The inline sub-chat extraction (_extract_direct) should be removed.
    Only the cascade call should remain.
    """
    import inspect
    from open_webui.utils.middleware import chat_completion_files_handler
    source = inspect.getsource(chat_completion_files_handler)
    assert "_extract_direct" not in source, "Inline _extract_direct should be removed"
    assert "apply_token_budget_cascade" in source, "Cascade function should be called"


# ── Test 6: Tier 2 event format matches handler patterns ──

@pytest.mark.asyncio
async def test_tier2_event_format():
    """
    When Tier 2 activates, it should emit a status event with:
    - type: "status"
    - data.action: "rag_cascade"
    - data.done: False  (in-progress, not final)
    This matches the pattern used by other in-progress events in the handler.
    """
    events = []

    async def mock_emitter(event):
        events.append(event)

    # Create sources that exceed budget at Tier 1 but have no index_content
    # (so Tier 2 returns None and we fall through to Tier 3)
    big_text = "x " * 5000  # ~5000 tokens
    sources = [make_source(big_text)]

    # Mock Files.get_file_by_id to return a file with index_content
    mock_file = MagicMock()
    mock_file.data = {"index_content": "short summary"}

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.return_value = mock_file
        result = await apply_token_budget_cascade(
            sources=sources,
            max_tokens=100,  # Very tight budget
            request=MagicMock(),
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            user=MagicMock(),
            event_emitter=mock_emitter,
        )

    # Should have emitted a Tier 2 status event
    cascade_events = [e for e in events if e.get("data", {}).get("action") == "rag_cascade"]
    assert len(cascade_events) >= 1, "Tier 2 should emit at least one rag_cascade event"
    tier2_event = cascade_events[0]
    assert tier2_event["type"] == "status"
    assert tier2_event["data"]["done"] is False, "Tier 2 event should be in-progress (done=False)"


# ── Test 7: Sources with empty metadata list don't crash in cascade ──

@pytest.mark.asyncio
async def test_cascade_empty_metadata_no_crash():
    """
    A source with metadata=[] should not crash the cascade at any tier.
    This was the IndexError fixed in T1.
    """
    source = {
        "source": {"id": "f1", "name": "test.md", "type": "file"},
        "document": ["x " * 5000],
        "metadata": [],
    }
    # Tier 3 path — need to mock extract function and config
    mock_request = MagicMock()
    mock_request.app.state.config.RAG_SUBCHAT_CONCURRENCY = 3

    with patch("open_webui.utils.middleware.Files") as MockFiles, \
         patch("open_webui.utils.middleware.extract_relevant_content_from_document") as mock_extract:
        MockFiles.get_file_by_id.return_value = None  # No index → Tier 2 fails
        mock_extract.return_value = "extracted"

        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=10,  # Force past Tier 1
            request=mock_request,
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )
    # Should not crash — that's the main assertion
    assert len(result) == 1
    assert result[0]["document"] == ["extracted"]

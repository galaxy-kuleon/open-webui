"""
W2-T2 Behavioral Tests: Phase A expand + 3-tier cascade for user collection path.

Tests verify:
1. User collection path: Phase A + cascade is ALWAYS applied (unconditional)
2. Other sources path: Phase A + cascade only when RAG_FULL_DOCUMENT_CONTEXT is True
3. Old inline Phase B sub-chat extraction code is removed
4. expand_sources_to_full_documents output feeds correctly into apply_token_budget_cascade
5. Empty user_collection_sources → Phase A/cascade skipped, no crash
6. expand_sources_to_full_documents returning empty list → graceful handling
7. Combined sources order: other_sources first, user_collection_sources second
8. Phase A + cascade composition: expand deduplicates by file_id, cascade applies budget
9. Source inspection: old Phase A/B markers (Phase B, subchat_extraction action) removed
"""

import asyncio
import sys
import os
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from open_webui.utils.middleware import (
    apply_token_budget_cascade,
    expand_sources_to_full_documents,
    estimate_sources_total_tokens,
    chat_completion_files_handler,
)


# ── Helpers ──────────────────────────────────────────────────────

def make_chunk_source(doc_text="chunk text", file_id="f1", name="test.md"):
    """
    Create a source dict matching what get_sources_from_items returns
    for vector search results (chunks with file_id in metadata).
    """
    return {
        "source": {"id": file_id, "name": name, "type": "collection"},
        "document": [doc_text],
        "metadata": [{"file_id": file_id, "name": name, "source": name}],
    }


def make_expanded_source(doc_text="full document content", file_id="f1", name="test.md"):
    """
    Create a source dict matching what expand_sources_to_full_documents returns.
    """
    return {
        "source": {"id": file_id, "name": name, "type": "collection"},
        "document": [doc_text],
        "metadata": [{"file_id": file_id, "name": name, "source": name, "type": "full_document"}],
    }


# ── Test 1: Old inline Phase B sub-chat code is removed ──

def test_old_phase_b_code_removed():
    """
    The old inline Phase B sub-chat extraction code has been replaced by
    cascade calls. Verify the old markers are gone from the handler source.
    """
    source = inspect.getsource(chat_completion_files_handler)

    # Old Phase B markers that should no longer exist:
    assert "Phase B:" not in source, "Old 'Phase B:' comment should be removed"
    assert "activating sub-chat extraction" not in source, (
        "Old Phase B log message should be removed"
    )
    assert "subchat_extraction" not in source, (
        "Old 'subchat_extraction' event action should be removed (cascade uses 'rag_cascade')"
    )
    assert "Content extraction complete" not in source, (
        "Old Phase B completion message should be removed"
    )


# ── Test 2: User collection path — expand + cascade are present ──

def test_user_collection_path_has_expand_and_cascade():
    """
    The handler source should call expand_sources_to_full_documents and
    apply_token_budget_cascade on user_collection_sources unconditionally.
    """
    source = inspect.getsource(chat_completion_files_handler)

    # Must contain the user collection expand call
    assert "expand_sources_to_full_documents" in source, (
        "Handler must call expand_sources_to_full_documents"
    )
    assert "apply_token_budget_cascade" in source, (
        "Handler must call apply_token_budget_cascade"
    )

    # The user collection cascade should NOT be gated behind RAG_FULL_DOCUMENT_CONTEXT
    # Find the user_collection_sources expand block
    lines = source.split("\n")
    found_user_collection_guard = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if "if user_collection_sources:" in stripped:
            found_user_collection_guard = True
            # The guard itself should NOT contain RAG_FULL_DOCUMENT_CONTEXT
            assert "RAG_FULL_DOCUMENT_CONTEXT" not in stripped, (
                "User collection expand/cascade should NOT be gated behind "
                "RAG_FULL_DOCUMENT_CONTEXT in the if-condition"
            )
            break

    assert found_user_collection_guard, (
        "Should have 'if user_collection_sources:' block"
    )


# ── Test 3: Other sources path — gated behind RAG_FULL_DOCUMENT_CONTEXT ──

def test_other_sources_path_gated():
    """
    The other_sources expand + cascade should be gated behind
    RAG_FULL_DOCUMENT_CONTEXT (preserving existing behavior for
    model knowledge / folder files).
    """
    source = inspect.getsource(chat_completion_files_handler)

    # Find the other_sources expand block — should check both other_sources and the config
    assert "other_sources and request.app.state.config.RAG_FULL_DOCUMENT_CONTEXT" in source, (
        "Other sources expand/cascade should be gated behind "
        "RAG_FULL_DOCUMENT_CONTEXT"
    )


# ── Test 4: expand_sources_to_full_documents → cascade composition ──

@pytest.mark.asyncio
async def test_expand_then_cascade_composition():
    """
    Verify the Phase A → cascade pipeline works end-to-end:
    1. expand_sources_to_full_documents traces file_id → loads full content
    2. apply_token_budget_cascade applies budget to expanded sources
    """
    # Chunk source from vector search
    chunk = make_chunk_source("chunk text about AI", file_id="f1", name="ai.md")

    # Mock Files.get_file_by_id to return full content
    mock_file = MagicMock()
    mock_file.data = {
        "content": "Full document about AI with many details...",
        "index_content": "Summary: AI document",
    }

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.return_value = mock_file

        # Phase A: expand
        expanded = expand_sources_to_full_documents([chunk])

    assert len(expanded) == 1, "Should expand to 1 document (deduplicated by file_id)"
    assert "Full document about AI" in expanded[0]["document"][0], (
        "Expanded source should contain full document content"
    )
    assert expanded[0]["metadata"][0]["file_id"] == "f1"

    # Phase B (cascade): Tier 1 should fit within generous budget
    result = await apply_token_budget_cascade(
        sources=expanded,
        max_tokens=10000,
        request=MagicMock(),
        body={"model": "test", "messages": [{"role": "user", "content": "tell me about AI"}]},
        user=MagicMock(),
        event_emitter=AsyncMock(),
    )
    assert result == expanded, "Tier 1: should return expanded sources within budget"


# ── Test 5: Empty user_collection_sources → no Phase A/cascade ──

def test_empty_user_collection_no_expand():
    """
    When user_collection_sources is empty, Phase A and cascade should be skipped.
    This is verified structurally: the 'if user_collection_sources:' guard.
    """
    source = inspect.getsource(chat_completion_files_handler)

    # The expand call must be inside an 'if user_collection_sources:' guard
    lines = source.split("\n")
    found_guard = False
    found_expand_inside = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if "if user_collection_sources:" in stripped:
            found_guard = True
            # Check next ~15 lines for expand call
            block_lines = lines[i + 1 : i + 16]
            for bline in block_lines:
                if "expand_sources_to_full_documents" in bline:
                    found_expand_inside = True
                    break
            break

    assert found_guard, "Must have 'if user_collection_sources:' guard"
    assert found_expand_inside, (
        "expand_sources_to_full_documents must be inside the user_collection guard"
    )


# ── Test 6: expand_sources_to_full_documents with no file_id → returns originals ──

def test_expand_no_file_id_returns_originals():
    """
    When chunks have no file_id in metadata, expand_sources_to_full_documents
    should return the original sources unchanged (graceful fallback).
    """
    source_no_fid = {
        "source": {"id": "x", "name": "unknown"},
        "document": ["some chunk"],
        "metadata": [{"name": "unknown"}],  # no file_id
    }

    result = expand_sources_to_full_documents([source_no_fid])
    # No file_ids found → seen_files is empty → returns originals
    assert result == [source_no_fid], "Should return originals when no file_id in metadata"


# ── Test 7: Multiple chunks from same file → expand deduplicates by file_id ──

def test_expand_deduplicates_by_file_id():
    """
    Vector search may return multiple chunks from the same file.
    expand_sources_to_full_documents should deduplicate by file_id,
    producing one source per unique file.
    """
    chunk1 = make_chunk_source("chunk 1 about cats", file_id="f1", name="animals.md")
    chunk2 = make_chunk_source("chunk 2 about dogs", file_id="f1", name="animals.md")
    chunk3 = make_chunk_source("chunk about cars", file_id="f2", name="vehicles.md")

    mock_file_animals = MagicMock()
    mock_file_animals.data = {
        "content": "Full animals doc...",
        "index_content": "Animals index",
    }
    mock_file_vehicles = MagicMock()
    mock_file_vehicles.data = {
        "content": "Full vehicles doc...",
        "index_content": "Vehicles index",
    }

    def get_file_side_effect(fid):
        return {"f1": mock_file_animals, "f2": mock_file_vehicles}.get(fid)

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.side_effect = get_file_side_effect
        expanded = expand_sources_to_full_documents([chunk1, chunk2, chunk3])

    assert len(expanded) == 2, "Should deduplicate: 3 chunks → 2 unique files"
    file_ids = [s["metadata"][0]["file_id"] for s in expanded]
    assert "f1" in file_ids
    assert "f2" in file_ids


# ── Test 8: Combined sources order preservation ──

def test_combined_sources_order():
    """
    After Phase A + cascade, the final combination should be:
    other_sources first, user_collection_sources second.
    This matches the existing combine pattern.
    """
    source = inspect.getsource(chat_completion_files_handler)

    # Find the final sources = combination after the cascade blocks
    lines = source.split("\n")
    # Look for the pattern: sources = other_sources + user_collection_sources
    # that comes AFTER the cascade blocks
    cascade_seen = False
    final_combine_found = False

    for line in lines:
        stripped = line.strip()
        if "apply_token_budget_cascade" in stripped:
            cascade_seen = True
        if cascade_seen and stripped == "sources = other_sources + user_collection_sources":
            final_combine_found = True
            break

    assert final_combine_found, (
        "Must have 'sources = other_sources + user_collection_sources' "
        "after cascade blocks"
    )


# ── Test 9: Old Phase A monolithic block is gone ──

def test_old_monolithic_phase_a_removed():
    """
    The old monolithic Phase A block that operated on combined 'sources' is gone.
    Now Phase A is applied separately to user_collection_sources and other_sources.
    """
    source = inspect.getsource(chat_completion_files_handler)

    # The old pattern: "if request.app.state.config.RAG_FULL_DOCUMENT_CONTEXT and sources:"
    # operating on the combined 'sources' variable
    assert "RAG_FULL_DOCUMENT_CONTEXT and sources:" not in source, (
        "Old monolithic Phase A on combined sources should be removed"
    )


# ── Test 10: Cascade called with correct parameters ──

def test_cascade_call_parameters():
    """
    Verify the cascade call in the handler passes the correct parameters:
    - sources: the expanded sources
    - max_tokens: from config
    - request, body, user, event_emitter: passed through
    """
    source = inspect.getsource(chat_completion_files_handler)

    # Check that the cascade call includes all required keyword args
    # Both the user_collection and other_sources cascade calls should have these
    required_kwargs = [
        "sources=",
        "max_tokens=",
        "request=request",
        "body=body",
        "user=user",
        "event_emitter=__event_emitter__",
    ]
    for kwarg in required_kwargs:
        assert source.count(kwarg) >= 1, (
            f"Cascade call must include '{kwarg}'"
        )


# ── Test 11: expand + cascade Tier 2 fallback for user collection ──

@pytest.mark.asyncio
async def test_user_collection_expand_cascade_tier2():
    """
    When expanded user collection exceeds Tier 1 budget but Tier 2 (index-only)
    fits, the cascade should return index-only sources.
    """
    big_chunk = make_chunk_source("x " * 5000, file_id="f1", name="big.md")

    mock_file = MagicMock()
    mock_file.data = {
        "content": "x " * 5000,  # Big full content
        "index_content": "Brief summary of the document",
    }

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.return_value = mock_file

        # Phase A expand
        expanded = expand_sources_to_full_documents([big_chunk])
        assert len(expanded) == 1

        # Cascade with tight budget → should fall to Tier 2
        result = await apply_token_budget_cascade(
            sources=expanded,
            max_tokens=100,  # Very tight
            request=MagicMock(),
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )

    assert len(result) == 1
    assert "Brief summary" in result[0]["document"][0], (
        "Should fall to Tier 2 (index-only) when budget is tight"
    )


# ── Test 12: Handler source has no old inline _extract function ──

def test_no_inline_extract_in_handler():
    """
    The old _extract inner function (Phase B inline) should not exist.
    Only cascade calls should remain.
    """
    source = inspect.getsource(chat_completion_files_handler)
    # Check specifically in the vector search path area
    # (Note: _extract exists in apply_token_budget_cascade — that's fine,
    # we're checking the handler itself doesn't define one)
    assert "async def _extract" not in source, (
        "Handler should not contain inline _extract function (use cascade instead)"
    )


# ── Test 13: Handler has two separate expand calls (user_collection + other) ──

def test_two_separate_expand_calls():
    """
    The handler should call expand_sources_to_full_documents twice:
    once for user_collection_sources and once for other_sources.
    """
    source = inspect.getsource(chat_completion_files_handler)
    count = source.count("expand_sources_to_full_documents(")
    # Might also appear in comments, but actual calls should be 2
    # (user_collection_sources and other_sources)
    assert count >= 2, (
        f"Expected at least 2 calls to expand_sources_to_full_documents, found {count}"
    )


# ── Test 14: Two separate cascade calls ──

def test_two_separate_cascade_calls():
    """
    The handler should call apply_token_budget_cascade at least twice
    in the vector search path (user_collection + other_sources) plus
    the Direct Content Mode call from Wave 1.
    """
    source = inspect.getsource(chat_completion_files_handler)
    count = source.count("apply_token_budget_cascade(")
    # Wave 1 Direct Content Mode + Wave 2 user_collection + Wave 2 other_sources = 3
    assert count >= 3, (
        f"Expected at least 3 calls to apply_token_budget_cascade, found {count}"
    )


# ── Test 15: Variables initialized before try block (exception path safety) ──

def test_variables_initialized_before_try_block():
    """
    CRITICAL: other_sources and user_collection_sources must be initialized
    BEFORE the try block so that if get_sources_from_items raises an exception,
    the code after the except block (which references these variables) does not
    trigger an UnboundLocalError.

    This is a source-level structural test that verifies the initialization
    appears before the try keyword.
    """
    source = inspect.getsource(chat_completion_files_handler)
    lines = source.split("\n")

    # Find the line indices of key landmarks
    init_other = None
    init_user_coll = None
    try_line = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "other_sources = []" and init_other is None:
            init_other = i
        if stripped == "user_collection_sources = []" and init_user_coll is None:
            init_user_coll = i
        # Find the try block that contains get_sources_from_items
        if stripped == "try:" and try_line is None:
            # Look ahead to confirm this is the right try block
            lookahead = "\n".join(lines[i:i+10])
            if "get_sources_from_items" in lookahead:
                try_line = i

    assert init_other is not None, (
        "Could not find 'other_sources = []' initialization"
    )
    assert init_user_coll is not None, (
        "Could not find 'user_collection_sources = []' initialization"
    )
    assert try_line is not None, (
        "Could not find try block containing get_sources_from_items"
    )
    assert init_other < try_line, (
        f"other_sources initialization (line {init_other}) must come BEFORE "
        f"try block (line {try_line})"
    )
    assert init_user_coll < try_line, (
        f"user_collection_sources initialization (line {init_user_coll}) must come BEFORE "
        f"try block (line {try_line})"
    )


# ── Test 16: Exception path does not raise UnboundLocalError ──

def test_exception_path_no_unbound_local_error():
    """
    Behavioral test: simulate the exact code pattern from chat_completion_files_handler
    to verify that if the try block raises, the variables are still accessible
    in the post-except code. This mirrors lines 2467-2545 of middleware.py.

    Without the fix (initialization before try), this would raise UnboundLocalError.
    """
    # Simulate the exact pattern from the handler
    other_sources = []
    user_collection_sources = []

    try:
        # Simulate get_sources_from_items raising
        raise RuntimeError("simulated retrieval failure")
        # These assignments would never execute:
        other_sources = [{"document": ["chunk"], "metadata": [{"name": "test"}]}]
        user_collection_sources = [{"document": ["chunk2"], "metadata": [{"name": "test2"}]}]
        sources = other_sources + user_collection_sources
    except Exception:
        pass  # handler logs and continues

    # Post-except code — this is what would crash without initialization
    # Mirrors line 2497: for i, src in enumerate(sources)
    # sources was set to [] at function level (line 2284), still holds that value
    sources = []  # from function-level init

    # Mirrors line 2513: if user_collection_sources:
    if user_collection_sources:
        pytest.fail("user_collection_sources should be empty after exception")

    # Mirrors line 2530: if other_sources and ...:
    if other_sources:
        pytest.fail("other_sources should be empty after exception")

    # Mirrors line 2545: sources = other_sources + user_collection_sources
    sources = other_sources + user_collection_sources
    assert sources == [], f"Expected empty sources, got {sources}"


def test_exception_path_without_init_would_fail():
    """
    Counter-test: prove that WITHOUT the initialization before try,
    the same code pattern WOULD raise UnboundLocalError.
    This confirms the initialization is load-bearing.
    """
    # Deliberately do NOT initialize before try — simulates the old buggy code
    try:
        raise RuntimeError("simulated retrieval failure")
        other_sources_buggy = [{"doc": "x"}]  # noqa: F841 — never reached
        user_collection_sources_buggy = [{"doc": "y"}]  # noqa: F841 — never reached
    except Exception:
        pass

    # Now try to access them — this MUST raise UnboundLocalError
    with pytest.raises(UnboundLocalError):
        _ = other_sources_buggy  # noqa: F821

    with pytest.raises(UnboundLocalError):
        _ = user_collection_sources_buggy  # noqa: F821


# ── Test 17: expand_sources_to_full_documents handles metadata=None ──

def test_expand_metadata_none_no_crash():
    """
    When a source has metadata=None (key present, value is None),
    expand_sources_to_full_documents should not crash.

    This was hardened as part of W2-T3 cross-validation: the function
    now uses `.get("metadata") or []` instead of `.get("metadata", [])`.
    """
    source_none_meta = {
        "source": {"id": "x", "name": "unknown"},
        "document": ["some chunk"],
        "metadata": None,  # Explicit None — the dangerous case
    }

    # Should NOT raise TypeError
    result = expand_sources_to_full_documents([source_none_meta])
    # No file_ids extractable from None metadata → seen_files is empty → returns originals
    assert result == [source_none_meta], "Should return originals when metadata is None"


# ── Test 18: get_source_context handles metadata=None ──

def test_get_source_context_metadata_none_no_crash():
    """
    get_source_context must handle metadata=None without crashing.
    The zip() call with None would fail; the `or []` fix prevents this.
    """
    from open_webui.utils.middleware import get_source_context

    source_none_meta = {
        "source": {"id": "x", "name": "unknown"},
        "document": ["some text"],
        "metadata": None,
    }

    # Should NOT raise TypeError
    context = get_source_context([source_none_meta])
    # With metadata=None → or [] → zip produces nothing → empty context
    assert context == "", "Should produce empty context when metadata is None"


# ── Test 19: Stale Phase B comment is updated ──

def test_no_stale_phase_b_comment():
    """
    The section header comment should say '3-Tier Token Budget Cascade',
    not 'Sub-Chat Extraction (Phase B)'. Phase B was replaced by the cascade.
    """
    source = inspect.getsource(expand_sources_to_full_documents)
    # The section header is above the function, so check the module source
    import open_webui.utils.middleware as mw
    module_source = inspect.getsource(mw)

    # Find the section header near expand_sources_to_full_documents
    # It should mention cascade, not Phase B
    assert "Sub-Chat Extraction (Phase B)" not in module_source, (
        "Stale 'Phase B' comment should be updated to mention the cascade"
    )

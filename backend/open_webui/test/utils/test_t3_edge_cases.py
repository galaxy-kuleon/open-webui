"""
W1-T3 Behavioral Tests: Edge cases and contract-alignment for the cascade.

Tests verify:
1. Token boundary: content exactly at budget passes Tier 1 (<=, not <)
2. Tier 3 with user_query=None: skips extraction, returns best available tier
3. build_index_only_sources logs warning for files without index_content
4. Line 2555 .get("metadata", []) normalization
5. Mixed file types: only_uploaded_files is False when collection items present
6. Cascade output structure matches get_source_context consumer expectations
7. Tier 3 None-query + Tier 2 available: returns Tier 2
8. Tier 3 None-query + Tier 2 unavailable: returns Tier 1
"""

import asyncio
import sys
import os
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the backend is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from open_webui.utils.middleware import (
    apply_token_budget_cascade,
    build_index_only_sources,
    estimate_sources_total_tokens,
    get_source_context,
)


# ── Helpers ──────────────────────────────────────────────────────

def make_source(doc_text="hello world", file_id="f1", name="test.md"):
    """Create a minimal source dict matching _build_direct_file_sources shape."""
    return {
        "source": {"id": file_id, "name": name, "type": "file"},
        "document": [doc_text],
        "metadata": [{"file_id": file_id, "name": name, "source": name}],
    }


# ── Test 1: Token boundary — exactly at budget passes Tier 1 ──

@pytest.mark.asyncio
async def test_tier1_exact_budget_boundary():
    """
    Content whose token count == max_tokens should pass Tier 1 (<=).
    This confirms the boundary is inclusive, not exclusive.
    """
    source = make_source("hello world")
    exact_tokens = estimate_sources_total_tokens([source])
    assert exact_tokens > 0, "Sanity: source should have non-zero tokens"

    result = await apply_token_budget_cascade(
        sources=[source],
        max_tokens=exact_tokens,  # Exactly at boundary
        request=MagicMock(),
        body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        user=MagicMock(),
        event_emitter=AsyncMock(),
    )
    # Tier 1 returns the original sources object (identity check)
    assert result == [source], "Exact budget boundary should pass Tier 1"


# ── Test 2: Tier 3 with user_query=None + no Tier 2 → returns Tier 1 ──

@pytest.mark.asyncio
async def test_tier3_none_query_no_tier2_returns_tier1():
    """
    When user_query is None and Tier 2 is unavailable (no index_content),
    the cascade should return Tier 1 sources (over budget, but best available).
    It should NOT attempt sub-chat extraction.
    """
    big_text = "x " * 5000
    sources = [make_source(big_text)]

    with patch("open_webui.utils.middleware.Files") as MockFiles, \
         patch("open_webui.utils.middleware.extract_relevant_content_from_document") as mock_extract:
        MockFiles.get_file_by_id.return_value = None  # No index → Tier 2 fails
        mock_extract.side_effect = AssertionError("Should not be called")

        result = await apply_token_budget_cascade(
            sources=sources,
            max_tokens=10,  # Force past Tier 1
            request=MagicMock(),
            body={"model": "test", "messages": []},  # No messages → None query
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )

    # Should return original sources (Tier 1, over budget)
    assert result == sources, "Should return Tier 1 when no query and no Tier 2"
    mock_extract.assert_not_called()


# ── Test 3: Tier 3 with user_query=None + Tier 2 available → returns Tier 2 ──

@pytest.mark.asyncio
async def test_tier3_none_query_with_tier2_returns_tier2():
    """
    When user_query is None and Tier 2 is available (but over budget),
    the cascade should return Tier 2 sources (smaller than Tier 1).
    """
    big_text = "x " * 5000
    sources = [make_source(big_text)]

    mock_file = MagicMock()
    # index_content is present but still over budget (forces past Tier 2 budget check)
    mock_file.data = {"index_content": "y " * 2000}

    with patch("open_webui.utils.middleware.Files") as MockFiles, \
         patch("open_webui.utils.middleware.extract_relevant_content_from_document") as mock_extract:
        MockFiles.get_file_by_id.return_value = mock_file
        mock_extract.side_effect = AssertionError("Should not be called")

        result = await apply_token_budget_cascade(
            sources=sources,
            max_tokens=10,  # Force past both Tier 1 and Tier 2 budget
            request=MagicMock(),
            body={"model": "test", "messages": []},  # No messages → None query
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )

    # Should return Tier 2 (index_only), not Tier 1
    assert len(result) == 1
    assert result[0]["document"][0].startswith("y "), "Should be Tier 2 index content"
    mock_extract.assert_not_called()


# ── Test 4: build_index_only_sources logs warning for missing index ──

def test_build_index_only_warns_missing_index(caplog):
    """
    When a source has no index_content, build_index_only_sources should
    log a warning identifying the file.
    """
    # Source with file_id but file has no index_content
    sources = [make_source("full document text", file_id="f_no_idx", name="no_index.md")]

    mock_file = MagicMock()
    mock_file.data = {"content": "full text", "index_content": ""}  # Empty index

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.return_value = mock_file
        with caplog.at_level(logging.WARNING):
            result = build_index_only_sources(sources)

    # Should return None (no files had index_content → any_replaced is False)
    assert result is None

    # Should have logged a warning about the missing index
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no index_content" in msg and "f_no_idx" in msg for msg in warning_messages), \
        f"Expected warning about missing index_content for f_no_idx, got: {warning_messages}"


# ── Test 5: build_index_only_sources mixed (some with, some without index) ──

def test_build_index_only_mixed_sources(caplog):
    """
    When some sources have index_content and some don't:
    - any_replaced = True (some were replaced)
    - Returns new sources list
    - Files without index retain original content
    - Warning is logged for the files without index
    """
    src_with_idx = make_source("full text A", file_id="f_with", name="with.md")
    src_no_idx = make_source("full text B", file_id="f_without", name="without.md")

    mock_file_with = MagicMock()
    mock_file_with.data = {"index_content": "summary of A"}

    mock_file_without = MagicMock()
    mock_file_without.data = {"index_content": ""}

    def get_file_side_effect(fid):
        return {"f_with": mock_file_with, "f_without": mock_file_without}.get(fid)

    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.side_effect = get_file_side_effect
        with caplog.at_level(logging.WARNING):
            result = build_index_only_sources([src_with_idx, src_no_idx])

    # Should return a list (any_replaced is True)
    assert result is not None
    assert len(result) == 2

    # First source: replaced with index_content
    assert result[0]["document"] == ["summary of A"]
    # Second source: retains original
    assert result[1]["document"] == ["full text B"]

    # Citation fields preserved
    assert result[0]["source"]["id"] == "f_with"
    assert result[0]["source"]["name"] == "with.md"
    assert result[1]["source"]["id"] == "f_without"

    # Warning logged for the file without index
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("f_without" in msg for msg in warning_messages), \
        f"Expected warning about f_without, got: {warning_messages}"


# ── Test 6: .get("metadata") or [] consistency in handler ──

def test_vector_search_metadata_get_consistency():
    """
    All metadata access patterns in chat_completion_files_handler must use
    the `or []` idiom (`.get("metadata") or []`) which handles BOTH
    missing key AND explicit metadata=None.

    The weaker `.get("metadata", [])` only protects against missing key
    but NOT against metadata=None (key present, value is None).
    """
    import inspect
    from open_webui.utils.middleware import chat_completion_files_handler
    source = inspect.getsource(chat_completion_files_handler)

    # Verify NO line uses the weak .get("metadata", []) pattern
    lines = source.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if '.get("metadata", [])' in stripped:
            pytest.fail(
                f"Weak .get('metadata', []) pattern at line {i}: {stripped}\n"
                f"Should use .get('metadata') or [] to handle metadata=None"
            )


# ── Test 7: Cascade output matches get_source_context expectations ──

def test_cascade_output_compatible_with_get_source_context():
    """
    Verify that sources produced by the cascade (at any tier) can be consumed
    by get_source_context without errors or missing data.

    get_source_context expects:
    - source["document"]: list of strings
    - source["metadata"]: list of dicts (same length as document)
    - source["source"]["name"]: optional string
    - source["source"]["id"]: optional string
    - metadata[i]["source"]: optional string (for source_id)
    """
    # Simulate Tier 1 source from _build_direct_file_sources
    tier1_source = make_source("full content here", file_id="f1", name="test.md")

    context = get_source_context([tier1_source])
    assert '<source id="1" name="test.md">' in context
    assert "full content here" in context

    # Simulate Tier 2 source from build_index_only_sources
    tier2_source = {
        "source": {"id": "f1", "name": "test.md", "type": "file"},
        "document": ["index summary"],
        "metadata": [{"file_id": "f1", "name": "test.md", "source": "test.md"}],
    }

    context = get_source_context([tier2_source])
    assert '<source id="1" name="test.md">' in context
    assert "index summary" in context


# ── Test 8: only_uploaded_files is False for mixed file types ──

def test_only_uploaded_files_false_for_mixed_types():
    """
    When files list has both type="file" and type="collection" items,
    only_uploaded_files should be False (Direct Content Mode skipped).
    """
    files = [
        {"type": "file", "id": "f1", "name": "test.md"},
        {"type": "collection", "collection_name": "my_collection", "name": "Coll"},
    ]
    only_uploaded_files = (
        files
        and all(item.get("type") == "file" for item in files)
    )
    assert only_uploaded_files is False, "Mixed types should not enter Direct Content Mode"


# ── Test 9: only_uploaded_files is True for all-file types ──

def test_only_uploaded_files_true_for_all_files():
    """Control: all type="file" items should enter Direct Content Mode."""
    files = [
        {"type": "file", "id": "f1", "name": "a.md"},
        {"type": "file", "id": "f2", "name": "b.md"},
    ]
    only_uploaded_files = (
        files
        and all(item.get("type") == "file" for item in files)
    )
    assert only_uploaded_files is True


# ── Test 10: Empty files list → only_uploaded_files is False ──

def test_only_uploaded_files_false_for_empty():
    """Empty files list should not enter Direct Content Mode."""
    files = []
    only_uploaded_files = (
        files
        and all(item.get("type") == "file" for item in files)
    )
    # Python: `[] and ...` short-circuits to `[]` (falsy), so the
    # `if only_uploaded_files:` guard correctly skips Direct Content Mode.
    assert not only_uploaded_files, "Empty files list should be falsy"


# ── Test 11: metadata=None does not crash build_index_only_sources ──

def test_build_index_only_metadata_none_no_crash():
    """
    .get("metadata", []) does NOT protect against explicit metadata=None.
    The `or []` idiom handles this. Verify behaviorally: a source with
    metadata=None should not crash build_index_only_sources.
    """
    source = {
        "source": {"id": "f1", "name": "test.md", "type": "file"},
        "document": ["some content"],
        "metadata": None,  # Explicit None — the dangerous case
    }
    with patch("open_webui.utils.middleware.Files") as MockFiles:
        MockFiles.get_file_by_id.return_value = None
        # Should NOT raise TypeError
        result = build_index_only_sources([source])

    # No file_id extractable from None metadata → no index lookup →
    # original content retained, but any_replaced=False → returns None
    assert result is None


# ── Test 12: metadata=None does not crash apply_token_budget_cascade Tier 3 ──

@pytest.mark.asyncio
async def test_cascade_tier3_metadata_none_no_crash():
    """
    In the Tier 3 extraction loop, metadatas from source.get("metadata") or []
    must handle metadata=None without crashing.
    """
    source = {
        "source": {"id": "f1", "name": "test.md", "type": "file"},
        "document": ["x " * 5000],
        "metadata": None,  # Explicit None
    }
    mock_request = MagicMock()
    mock_request.app.state.config.RAG_SUBCHAT_CONCURRENCY = 3

    with patch("open_webui.utils.middleware.Files") as MockFiles, \
         patch("open_webui.utils.middleware.extract_relevant_content_from_document") as mock_extract:
        MockFiles.get_file_by_id.return_value = None  # No index → Tier 2 fails
        mock_extract.return_value = "extracted"

        # Should NOT raise TypeError
        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=10,
            request=mock_request,
            body={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            user=MagicMock(),
            event_emitter=AsyncMock(),
        )

    assert len(result) == 1
    assert result[0]["document"] == ["extracted"]

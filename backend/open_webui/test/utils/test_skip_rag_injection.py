"""
Behavioral tests for skip_rag docling-based MD injection.

Tests the two-path data flow:
  - PDF/DOCX/PPTX/XLSX → docling CLI → markdown → inject into prompt
  - Plain text/markdown → file.data["content"] → inject into prompt

Since the injection logic is inline in process_chat_payload(), we test by
simulating the same data flow with the same functions it calls.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from open_webui.utils.misc import (
    add_or_update_system_message,
    add_or_update_user_message,
)
from open_webui.utils.docling import SUPPORTED_EXTENSIONS as DOCLING_EXTS


# ── Helpers ─────────────────────────────────────────────────────


def make_file_obj(filename, *, content=None, docling_md=None, path=None, data_override=None):
    """Create a file object matching Files.get_file_by_id() shape."""
    if data_override is not None:
        data = data_override
    else:
        data = {}
        if content is not None:
            data["content"] = content
        if docling_md is not None:
            data["docling_md"] = docling_md
        data = data if data else None
    return SimpleNamespace(
        filename=filename,
        data=data,
        path=path,
    )


async def build_skip_rag_context(
    files, get_file_fn, docling_convert_fn, update_data_fn=None,
    read_raw_fn=None,
):
    """
    Pure-async function replicating the skip_rag injection logic
    from middleware.py.  Returns (context_parts, sources).

    Args:
        files: list of {"id": ..., "name": ...} dicts
        get_file_fn: file_id → file_obj (or None)
        docling_convert_fn: async (raw_path) → markdown string
        update_data_fn: (file_id, data_dict) → None (for caching)
        read_raw_fn: (path) → str (read raw file from disk; for testing)
    """
    context_parts = []
    sources = []

    for item in files:
        file_id = item.get("id")
        if not file_id:
            continue

        file_obj = get_file_fn(file_id)
        if not file_obj:
            continue

        filename = item.get("name") or file_obj.filename
        ext = Path(filename).suffix.lower()
        content = None

        # ── Docling path: PDF, DOCX, PPTX, XLSX ──
        if ext in DOCLING_EXTS:
            cached_md = (file_obj.data or {}).get("docling_md")
            if cached_md:
                content = cached_md
            else:
                if not file_obj.path:
                    continue
                try:
                    content = await docling_convert_fn(file_obj.path)
                    if update_data_fn:
                        update_data_fn(file_id, {"docling_md": content})
                except Exception:
                    continue
        else:
            # ── Plain text / markdown / other ──
            # Try data["content"] first; fall back to raw file on disk
            content = ((file_obj.data or {}).get("content") or "").strip()
            if not content and file_obj.path and read_raw_fn:
                try:
                    content = read_raw_fn(file_obj.path).strip()
                except Exception:
                    pass

        if not content:
            continue

        context_parts.append(f"## File: {filename}\n\n{content}")
        sources.append(
            {
                "source": {"id": file_id, "name": filename, "type": "file"},
                "document": [content],
                "metadata": [
                    {"file_id": file_id, "name": filename, "source": filename}
                ],
            }
        )

    return context_parts, sources


# ── Docling path tests ──────────────────────────────────────────


class TestDoclingPath:
    """Test the docling conversion path for PDF/DOCX files."""

    @pytest.mark.asyncio
    async def test_pdf_runs_docling(self):
        files = [{"id": "f1", "name": "report.pdf"}]
        db = {"f1": make_file_obj("report.pdf", path="/uploads/report.pdf")}

        mock_docling = AsyncMock(return_value="# Docling Output\n\nConverted text")

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        mock_docling.assert_awaited_once_with("/uploads/report.pdf")
        assert len(parts) == 1
        assert "# Docling Output" in parts[0]
        assert "Converted text" in parts[0]
        assert sources[0]["document"] == ["# Docling Output\n\nConverted text"]

    @pytest.mark.asyncio
    async def test_docx_runs_docling(self):
        files = [{"id": "f1", "name": "doc.docx"}]
        db = {"f1": make_file_obj("doc.docx", path="/uploads/doc.docx")}

        mock_docling = AsyncMock(return_value="DOCX content as markdown")

        parts, _ = await build_skip_rag_context(files, db.get, mock_docling)

        mock_docling.assert_awaited_once()
        assert len(parts) == 1
        assert "DOCX content as markdown" in parts[0]

    @pytest.mark.asyncio
    async def test_cached_docling_md_skips_conversion(self):
        """When docling_md is cached, docling CLI is NOT called."""
        files = [{"id": "f1", "name": "report.pdf"}]
        db = {
            "f1": make_file_obj(
                "report.pdf",
                docling_md="# Cached MD\n\nPreviously converted",
                path="/uploads/report.pdf",
            )
        }

        mock_docling = AsyncMock(side_effect=AssertionError("should not be called"))

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        mock_docling.assert_not_awaited()
        assert len(parts) == 1
        assert "# Cached MD" in parts[0]

    @pytest.mark.asyncio
    async def test_docling_result_is_cached(self):
        """After docling runs, result is passed to update_data_fn for caching."""
        files = [{"id": "f1", "name": "report.pdf"}]
        db = {"f1": make_file_obj("report.pdf", path="/uploads/report.pdf")}

        mock_docling = AsyncMock(return_value="Fresh MD output")
        cached = {}

        def mock_update(file_id, data):
            cached[file_id] = data

        parts, _ = await build_skip_rag_context(
            files, db.get, mock_docling, update_data_fn=mock_update
        )

        assert "f1" in cached
        assert cached["f1"]["docling_md"] == "Fresh MD output"

    @pytest.mark.asyncio
    async def test_docling_failure_skips_file(self):
        """When docling fails, the file is skipped entirely (no fallback)."""
        files = [{"id": "f1", "name": "bad.pdf"}]
        db = {
            "f1": make_file_obj(
                "bad.pdf",
                content="fallback content that should NOT be used",
                path="/uploads/bad.pdf",
            )
        }

        mock_docling = AsyncMock(side_effect=RuntimeError("docling crashed"))

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        assert parts == []
        assert sources == []

    @pytest.mark.asyncio
    async def test_pdf_without_storage_path_skipped(self):
        """PDF with no file.path is skipped (can't run docling without raw file)."""
        files = [{"id": "f1", "name": "orphan.pdf"}]
        db = {"f1": make_file_obj("orphan.pdf", path=None)}

        mock_docling = AsyncMock(side_effect=AssertionError("should not be called"))

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        mock_docling.assert_not_awaited()
        assert parts == []

    @pytest.mark.asyncio
    async def test_pptx_and_xlsx_also_use_docling(self):
        """All SUPPORTED_EXTENSIONS go through docling path."""
        files = [
            {"id": "f1", "name": "slides.pptx"},
            {"id": "f2", "name": "data.xlsx"},
        ]
        db = {
            "f1": make_file_obj("slides.pptx", path="/uploads/slides.pptx"),
            "f2": make_file_obj("data.xlsx", path="/uploads/data.xlsx"),
        }

        call_log = []

        async def mock_docling(path):
            call_log.append(path)
            return f"Converted from {Path(path).suffix}"

        parts, _ = await build_skip_rag_context(files, db.get, mock_docling)

        assert len(call_log) == 2
        assert len(parts) == 2


# ── Plain text path tests ───────────────────────────────────────


class TestPlainTextPath:
    """Test the direct content path for non-docling file types."""

    @pytest.mark.asyncio
    async def test_markdown_file_uses_data_content(self):
        files = [{"id": "f1", "name": "notes.md"}]
        db = {"f1": make_file_obj("notes.md", content="# My Notes\n\nSome text")}

        mock_docling = AsyncMock(side_effect=AssertionError("should not be called"))

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        mock_docling.assert_not_awaited()
        assert len(parts) == 1
        assert "# My Notes" in parts[0]

    @pytest.mark.asyncio
    async def test_txt_file_uses_data_content(self):
        files = [{"id": "f1", "name": "readme.txt"}]
        db = {"f1": make_file_obj("readme.txt", content="Plain text here")}

        mock_docling = AsyncMock()

        parts, _ = await build_skip_rag_context(files, db.get, mock_docling)

        mock_docling.assert_not_awaited()
        assert len(parts) == 1
        assert "Plain text here" in parts[0]

    @pytest.mark.asyncio
    async def test_raw_file_fallback_when_no_data_content(self):
        """When data['content'] is empty (process=false), read raw file from disk."""
        files = [{"id": "f1", "name": "notes.md"}]
        # data_override={} simulates process=false: no content extracted
        db = {
            "f1": make_file_obj(
                "notes.md", data_override={}, path="/uploads/notes.md"
            )
        }

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock(),
            read_raw_fn=lambda p: "# Raw file content from disk",
        )

        assert len(parts) == 1
        assert "# Raw file content from disk" in parts[0]

    @pytest.mark.asyncio
    async def test_raw_file_fallback_not_used_when_data_content_exists(self):
        """When data['content'] is present, raw file is NOT read."""
        files = [{"id": "f1", "name": "notes.md"}]
        db = {"f1": make_file_obj("notes.md", content="From data", path="/uploads/x")}

        read_called = []

        def spy_read(p):
            read_called.append(p)
            return "Should not appear"

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock(), read_raw_fn=spy_read,
        )

        assert len(parts) == 1
        assert "From data" in parts[0]
        assert len(read_called) == 0

    @pytest.mark.asyncio
    async def test_raw_file_fallback_failure_skips(self):
        """When raw file read fails, file is skipped."""
        files = [{"id": "f1", "name": "broken.md"}]
        db = {
            "f1": make_file_obj(
                "broken.md", data_override={}, path="/uploads/broken.md"
            )
        }

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock(),
            read_raw_fn=lambda p: (_ for _ in ()).throw(IOError("disk error")),
        )

        assert parts == []

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self):
        files = [{"id": "f1", "name": "empty.md"}]
        db = {"f1": make_file_obj("empty.md", content="")}

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )

        assert parts == []

    @pytest.mark.asyncio
    async def test_whitespace_only_content_skipped(self):
        files = [{"id": "f1", "name": "ws.md"}]
        db = {"f1": make_file_obj("ws.md", content="   \n\n  ")}

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )

        assert parts == []

    @pytest.mark.asyncio
    async def test_none_data_skipped(self):
        """file.data is None — no content available."""
        files = [{"id": "f1", "name": "empty.txt"}]
        obj = SimpleNamespace(filename="empty.txt", data=None, path=None)
        db = {"f1": obj}

        parts, _ = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )

        assert parts == []


# ── Mixed scenario tests ────────────────────────────────────────


class TestMixedScenarios:
    """Test combinations of docling and plain text files."""

    @pytest.mark.asyncio
    async def test_mixed_pdf_and_markdown(self):
        files = [
            {"id": "f1", "name": "report.pdf"},
            {"id": "f2", "name": "notes.md"},
        ]
        db = {
            "f1": make_file_obj("report.pdf", path="/uploads/report.pdf"),
            "f2": make_file_obj("notes.md", content="# Notes"),
        }

        mock_docling = AsyncMock(return_value="# PDF Content from docling")

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        assert len(parts) == 2
        assert "# PDF Content from docling" in parts[0]
        assert "# Notes" in parts[1]
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid(self):
        """Docling failure + missing file + valid markdown = only markdown survives."""
        files = [
            {"id": "f1", "name": "broken.pdf"},
            {"id": "missing"},
            {"id": "f3", "name": "good.md"},
        ]
        db = {
            "f1": make_file_obj("broken.pdf", path="/uploads/broken.pdf"),
            "f3": make_file_obj("good.md", content="Good content"),
        }

        mock_docling = AsyncMock(side_effect=RuntimeError("docling failed"))

        parts, sources = await build_skip_rag_context(
            files, db.get, mock_docling
        )

        assert len(parts) == 1
        assert "good.md" in parts[0]

    @pytest.mark.asyncio
    async def test_no_id_skipped(self):
        files = [{"name": "no-id.md"}]

        parts, _ = await build_skip_rag_context(
            files, lambda _: None, AsyncMock()
        )

        assert parts == []

    @pytest.mark.asyncio
    async def test_empty_file_list(self):
        parts, sources = await build_skip_rag_context(
            [], lambda _: None, AsyncMock()
        )

        assert parts == []
        assert sources == []

    @pytest.mark.asyncio
    async def test_filename_fallback_to_file_obj(self):
        """When item has no 'name', falls back to file_obj.filename."""
        files = [{"id": "f1"}]  # no 'name' key
        db = {"f1": make_file_obj("from-db.md", content="Fallback name test")}

        parts, sources = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )

        assert "## File: from-db.md" in parts[0]
        assert sources[0]["source"]["name"] == "from-db.md"


# ── Message injection tests ─────────────────────────────────────


class TestMessageInjection:
    """Test that injection into messages works correctly via the real functions."""

    def test_system_message_injection_appends(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Summarize this file."},
        ]
        context = "## File: report.pdf\n\n# Docling output here"

        result = add_or_update_system_message(context, messages, append=True)

        sys_content = result[0]["content"]
        assert "You are a helpful assistant." in sys_content
        assert "## File: report.pdf" in sys_content
        assert "# Docling output here" in sys_content

    def test_system_message_injection_creates_if_absent(self):
        messages = [
            {"role": "user", "content": "Summarize this file."},
        ]
        context = "## File: report.pdf\n\nSome content"

        result = add_or_update_system_message(context, messages, append=True)

        assert result[0]["role"] == "system"
        assert "## File: report.pdf" in result[0]["content"]
        assert result[1]["role"] == "user"

    def test_user_message_injection_prepends(self):
        messages = [
            {"role": "user", "content": "What does this document say?"},
        ]
        context = "## File: doc.pdf\n\nDocument body from docling"

        result = add_or_update_user_message(context, messages, append=False)

        user_content = result[-1]["content"]
        assert "## File: doc.pdf" in user_content
        assert "What does this document say?" in user_content


# ── Source shape tests ──────────────────────────────────────────


class TestSourceShape:
    """Verify the source dict shape matches what the citation UI expects."""

    @pytest.mark.asyncio
    async def test_source_has_required_keys(self):
        files = [{"id": "abc", "name": "test.pdf"}]
        db = {
            "abc": make_file_obj(
                "test.pdf", docling_md="Test content", path="/uploads/test.pdf"
            )
        }

        _, sources = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )
        src = sources[0]

        assert "source" in src
        assert "name" in src["source"]
        assert "id" in src["source"]
        assert "type" in src["source"]

        assert "document" in src
        assert isinstance(src["document"], list)
        assert "metadata" in src
        assert isinstance(src["metadata"], list)
        assert "file_id" in src["metadata"][0]
        assert "name" in src["metadata"][0]
        assert "source" in src["metadata"][0]

    @pytest.mark.asyncio
    async def test_source_passes_citation_filter(self):
        """
        The citation filter keeps sources where source.name or source.id
        is truthy.  Verify our sources pass.
        """
        files = [{"id": "f1", "name": "doc.pdf"}]
        db = {
            "f1": make_file_obj(
                "doc.pdf", docling_md="Content", path="/uploads/doc.pdf"
            )
        }

        _, sources = await build_skip_rag_context(
            files, db.get, AsyncMock()
        )

        filtered = [
            s
            for s in sources
            if s.get("source", {}).get("name", "")
            or s.get("source", {}).get("id", "")
        ]
        assert len(filtered) == 1, "Source should pass the citation filter"

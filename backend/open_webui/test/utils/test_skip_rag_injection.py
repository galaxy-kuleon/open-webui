"""
Behavioral tests for skip_rag direct-injection context building.

All tests call the REAL ``build_skip_rag_context`` from ``utils.skip_rag``
and assert on named fields of the returned ``SkipRagContext`` dataclass.

No inline replica of injection logic — the function IS the system under test.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_webui.utils.skip_rag import (
    SkipRagContext,
    _SKIP_RAG_PREAMBLE,
    build_skip_rag_context,
)
from open_webui.utils.docling import SUPPORTED_EXTENSIONS as DOCLING_EXTS


# ── Fixtures / helpers ───────────────────────────────────────────────────────


def make_file_obj(server_filename, *, content=None, docling_md=None, path=None, data_override=None):
    """Create a file object matching Files.get_file_by_id() shape.

    ``server_filename`` is what file_obj.filename returns — the server-side
    authoritative name (used for extension routing).
    """
    if data_override is not None:
        data = data_override
    else:
        data = {}
        if content is not None:
            data['content'] = content
        if docling_md is not None:
            data['docling_md'] = docling_md
        data = data if data else None
    return SimpleNamespace(
        filename=server_filename,
        data=data,
        path=path,
    )


def _noop_update(file_id, data):
    pass


def _noop_storage(path):
    return path


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _call(
    files_list,
    file_db,
    *,
    messages=None,
    docling_fn=None,
    storage_fn=None,
    update_fn=None,
):
    """Thin wrapper: call build_skip_rag_context with sensible defaults."""
    if messages is None:
        messages = [{'role': 'user', 'content': 'Summarize.'}]
    if docling_fn is None:
        docling_fn = AsyncMock(side_effect=AssertionError('docling called unexpectedly'))
    if storage_fn is None:
        storage_fn = _noop_storage
    if update_fn is None:
        update_fn = _noop_update

    async def _async_get_file(file_id):
        return file_db.get(file_id)

    return await build_skip_rag_context(
        files_list=files_list,
        messages=messages,
        get_file_fn=_async_get_file,
        update_file_fn=update_fn,
        docling_convert_fn=docling_fn,
        storage_get_file_fn=storage_fn,
        docling_exts=DOCLING_EXTS,
    )


# ── Return-type contract ─────────────────────────────────────────────────────


class TestReturnTypeContract:
    """Verify SkipRagContext fields exist and have correct types."""

    @pytest.mark.asyncio
    async def test_returns_skip_rag_context_instance(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result, SkipRagContext)

    @pytest.mark.asyncio
    async def test_context_block_is_str(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.context_block, str)

    @pytest.mark.asyncio
    async def test_preamble_needed_is_bool(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.preamble_needed, bool)

    @pytest.mark.asyncio
    async def test_truncation_events_is_list(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.truncation_events, list)

    @pytest.mark.asyncio
    async def test_double_injection_applied_is_bool(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.double_injection_applied, bool)

    @pytest.mark.asyncio
    async def test_server_side_filename_used_is_bool(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.server_side_filename_used, bool)

    @pytest.mark.asyncio
    async def test_sources_is_list(self):
        file_db = {'f1': make_file_obj('notes.md', content='Hello')}
        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)
        assert isinstance(result.sources, list)


# ── Docling path tests ───────────────────────────────────────────────────────


class TestDoclingPath:
    """Test the docling conversion path for PDF/DOCX files."""

    @pytest.mark.asyncio
    async def test_pdf_runs_docling(self):
        file_db = {'f1': make_file_obj('report.pdf', path='/uploads/report.pdf')}
        mock_docling = AsyncMock(return_value='# Docling Output\n\nConverted text')
        storage_calls = []

        def spy_storage(p):
            storage_calls.append(p)
            return p

        result = await _call(
            [{'id': 'f1', 'name': 'report.pdf'}],
            file_db,
            docling_fn=mock_docling,
            storage_fn=spy_storage,
        )

        mock_docling.assert_awaited_once()
        assert len(storage_calls) == 1
        # context_block contains the delimited content
        assert '# Docling Output' in result.context_block
        assert 'Converted text' in result.context_block
        # sanitizer was invoked
        assert result.double_injection_applied is True
        # at least one source
        assert len(result.sources) == 1
        assert result.sources[0]['document'] == ['# Docling Output\n\nConverted text']

    @pytest.mark.asyncio
    async def test_docx_runs_docling(self):
        file_db = {'f1': make_file_obj('doc.docx', path='/uploads/doc.docx')}
        mock_docling = AsyncMock(return_value='DOCX content as markdown')

        result = await _call(
            [{'id': 'f1', 'name': 'doc.docx'}],
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        mock_docling.assert_awaited_once()
        assert 'DOCX content as markdown' in result.context_block
        assert result.double_injection_applied is True

    @pytest.mark.asyncio
    async def test_cached_docling_md_skips_conversion(self):
        """When docling_md is cached, docling CLI is NOT called."""
        file_db = {
            'f1': make_file_obj(
                'report.pdf',
                docling_md='# Cached MD\n\nPreviously converted',
                path='/uploads/report.pdf',
            )
        }
        mock_docling = AsyncMock(side_effect=AssertionError('should not be called'))

        result = await _call(
            [{'id': 'f1', 'name': 'report.pdf'}],
            file_db,
            docling_fn=mock_docling,
        )

        mock_docling.assert_not_awaited()
        assert '# Cached MD' in result.context_block
        assert result.double_injection_applied is True

    @pytest.mark.asyncio
    async def test_docling_result_is_cached(self):
        """After docling runs, result is passed to update_file_fn for caching."""
        file_db = {'f1': make_file_obj('report.pdf', path='/uploads/report.pdf')}
        mock_docling = AsyncMock(return_value='Fresh MD output')
        cached = {}

        def mock_update(file_id, data):
            cached[file_id] = data

        result = await _call(
            [{'id': 'f1', 'name': 'report.pdf'}],
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
            update_fn=mock_update,
        )

        assert 'f1' in cached
        assert cached['f1']['docling_md'] == 'Fresh MD output'
        assert 'Fresh MD output' in result.context_block

    @pytest.mark.asyncio
    async def test_docling_failure_skips_file(self):
        """When docling fails, the file is skipped entirely (no fallback)."""
        file_db = {
            'f1': make_file_obj(
                'bad.pdf',
                content='fallback content that should NOT be used',
                path='/uploads/bad.pdf',
            )
        }
        mock_docling = AsyncMock(side_effect=RuntimeError('docling crashed'))

        result = await _call(
            [{'id': 'f1', 'name': 'bad.pdf'}],
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        assert result.context_block == ''
        assert result.sources == []
        assert result.double_injection_applied is False

    @pytest.mark.asyncio
    async def test_pdf_without_storage_path_skipped(self):
        """PDF with no file.path is skipped (can't run docling without raw file)."""
        file_db = {'f1': make_file_obj('orphan.pdf', path=None)}
        mock_docling = AsyncMock(side_effect=AssertionError('should not be called'))

        result = await _call(
            [{'id': 'f1', 'name': 'orphan.pdf'}],
            file_db,
            docling_fn=mock_docling,
        )

        mock_docling.assert_not_awaited()
        assert result.context_block == ''
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_pptx_and_xlsx_also_use_docling(self):
        """All SUPPORTED_EXTENSIONS go through docling path."""
        file_db = {
            'f1': make_file_obj('slides.pptx', path='/uploads/slides.pptx'),
            'f2': make_file_obj('data.xlsx', path='/uploads/data.xlsx'),
        }
        call_log = []

        async def spy_docling(path):
            call_log.append(path)
            return f'Converted from {Path(path).suffix}'

        result = await _call(
            [
                {'id': 'f1', 'name': 'slides.pptx'},
                {'id': 'f2', 'name': 'data.xlsx'},
            ],
            file_db,
            docling_fn=spy_docling,
            storage_fn=lambda p: p,
        )

        assert len(call_log) == 2
        assert len(result.sources) == 2
        # delimited blocks should both appear in context_block
        assert '<<FILE file-f1 BEGIN>>' in result.context_block
        assert '<<FILE file-f2 BEGIN>>' in result.context_block


# ── Plain text path tests ────────────────────────────────────────────────────


class TestPlainTextPath:
    """Test the direct content path for non-docling file types."""

    @pytest.mark.asyncio
    async def test_markdown_file_uses_data_content(self):
        file_db = {'f1': make_file_obj('notes.md', content='# My Notes\n\nSome text')}

        result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)

        assert '# My Notes' in result.context_block
        assert result.double_injection_applied is True
        assert result.preamble_needed is True

    @pytest.mark.asyncio
    async def test_txt_file_uses_data_content(self):
        file_db = {'f1': make_file_obj('readme.txt', content='Plain text here')}

        result = await _call([{'id': 'f1', 'name': 'readme.txt'}], file_db)

        assert 'Plain text here' in result.context_block
        assert len(result.sources) == 1

    @pytest.mark.asyncio
    async def test_raw_file_fallback_when_no_data_content(self):
        """When data['content'] is empty (process=false), read raw file from disk."""
        import tempfile, os

        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write('# Raw file content from disk')
            tmp_path = f.name

        try:
            # data_override={} simulates process=false: no content extracted
            file_db = {'f1': make_file_obj('notes.md', data_override={}, path=tmp_path)}

            result = await _call([{'id': 'f1', 'name': 'notes.md'}], file_db)

            assert '# Raw file content from disk' in result.context_block
            assert result.double_injection_applied is True
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_raw_file_fallback_not_used_when_data_content_exists(self):
        """When data['content'] is present, raw file is NOT read."""
        file_db = {'f1': make_file_obj('notes.md', content='From data', path='/never/read')}

        # storage_fn would be called only if raw file fallback fires
        storage_called = []

        def spy_storage(p):
            storage_called.append(p)
            raise AssertionError('raw file should not be read when data content exists')

        result = await _call(
            [{'id': 'f1', 'name': 'notes.md'}],
            file_db,
            storage_fn=spy_storage,
        )

        assert 'From data' in result.context_block
        assert storage_called == []

    @pytest.mark.asyncio
    async def test_raw_file_fallback_failure_skips(self):
        """When raw file read fails (storage_fn raises), file is skipped."""
        import tempfile, os

        # data_override={} = no content in DB; path set but storage fails
        file_db = {'f1': make_file_obj('broken.md', data_override={}, path='/nonexistent/path.md')}

        # storage_fn returns a path that doesn't exist → read_text will fail
        result = await _call(
            [{'id': 'f1', 'name': 'broken.md'}],
            file_db,
            storage_fn=lambda p: p,  # returns the nonexistent path
        )

        assert result.context_block == ''
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self):
        file_db = {'f1': make_file_obj('empty.md', content='')}

        result = await _call([{'id': 'f1', 'name': 'empty.md'}], file_db)

        assert result.context_block == ''
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_whitespace_only_content_skipped(self):
        file_db = {'f1': make_file_obj('ws.md', content='   \n\n  ')}

        result = await _call([{'id': 'f1', 'name': 'ws.md'}], file_db)

        assert result.context_block == ''
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_none_data_skipped(self):
        """file.data is None — no content available."""
        obj = SimpleNamespace(filename='empty.txt', data=None, path=None)
        file_db = {'f1': obj}

        result = await _call([{'id': 'f1', 'name': 'empty.txt'}], file_db)

        assert result.context_block == ''
        assert result.sources == []


# ── Delimiter / sanitizer tests ──────────────────────────────────────────────


class TestDelimiterAndSanitizer:
    """Verify delimiter wrapping and sanitizer invocation."""

    @pytest.mark.asyncio
    async def test_delimiter_wrapping_correct(self):
        file_db = {'f1': make_file_obj('doc.txt', content='Content here')}

        result = await _call([{'id': 'f1', 'name': 'doc.txt'}], file_db)

        assert '<<FILE file-f1 BEGIN>>' in result.context_block
        assert '<<FILE file-f1 END>>' in result.context_block
        assert 'Content here' in result.context_block

    @pytest.mark.asyncio
    async def test_two_files_delimited_separately(self):
        file_db = {
            'f1': make_file_obj('one.txt', content='First content'),
            'f2': make_file_obj('two.txt', content='Second content'),
        }

        result = await _call(
            [{'id': 'f1', 'name': 'one.txt'}, {'id': 'f2', 'name': 'two.txt'}],
            file_db,
        )

        assert '<<FILE file-f1 BEGIN>>' in result.context_block
        assert '<<FILE file-f1 END>>' in result.context_block
        assert '<<FILE file-f2 BEGIN>>' in result.context_block
        assert '<<FILE file-f2 END>>' in result.context_block
        assert 'First content' in result.context_block
        assert 'Second content' in result.context_block

    @pytest.mark.asyncio
    async def test_sanitization_strips_unicode_bidi(self):
        """Unicode bidi + C0 control chars stripped from injected file content."""
        dirty = 'Safe\u2028bidi\u202ahere'
        file_db = {'f1': make_file_obj('doc.txt', content=dirty)}

        result = await _call([{'id': 'f1', 'name': 'doc.txt'}], file_db)

        assert '\u2028' not in result.context_block
        assert '\u202a' not in result.context_block
        assert 'Safe' in result.context_block
        assert result.double_injection_applied is True

    @pytest.mark.asyncio
    async def test_empty_after_sanitization_skipped(self):
        """File whose content is entirely control chars is skipped."""
        only_controls = '\u2028\u2029\u200b\u202a'
        file_db = {'f1': make_file_obj('empty.txt', content=only_controls)}

        result = await _call([{'id': 'f1', 'name': 'empty.txt'}], file_db)

        assert result.context_block == ''
        assert result.sources == []


# ── Byte-cap truncation tests ────────────────────────────────────────────────


class TestByteCap:
    """Verify byte-cap truncation path."""

    @pytest.mark.asyncio
    async def test_over_cap_file_is_truncated(self):
        """File exceeding 256 KiB UTF-8 bytes is truncated; END marker preserved."""
        from open_webui.utils.sanitize import _SKIP_RAG_MAX_BYTES

        large_content = 'X' * (300 * 1024)  # 300 KiB
        file_db = {'bigfile': make_file_obj('big.txt', content=large_content)}

        result = await _call([{'id': 'bigfile', 'name': 'big.txt'}], file_db)

        assert '<<FILE file-bigfile BEGIN>>' in result.context_block
        assert '<<FILE file-bigfile END>>' in result.context_block

        block_bytes = len(result.context_block.encode('utf-8'))
        end_marker = '<<FILE file-bigfile END>>'
        max_allowed = _SKIP_RAG_MAX_BYTES + len(end_marker.encode('utf-8')) + 2
        assert block_bytes <= max_allowed

    @pytest.mark.asyncio
    async def test_truncation_event_recorded_in_result(self):
        """Truncation event is collected in truncation_events (not emitted directly)."""
        from open_webui.utils.sanitize import _SKIP_RAG_MAX_BYTES

        large_content = 'Y' * (300 * 1024)  # 300 KiB
        file_db = {'captest': make_file_obj('cap.txt', content=large_content)}

        result = await _call([{'id': 'captest', 'name': 'cap.txt'}], file_db)

        # Exactly one truncation event recorded
        assert len(result.truncation_events) == 1
        ev = result.truncation_events[0]
        assert ev['type'] == 'status'
        assert ev['data']['action'] == 'skip_rag_truncated'
        assert ev['data']['file_id'] == 'captest'
        assert ev['data']['original_size'] > _SKIP_RAG_MAX_BYTES
        assert ev['data']['done'] is True

    @pytest.mark.asyncio
    async def test_no_truncation_event_for_small_file(self):
        file_db = {'f1': make_file_obj('small.txt', content='Tiny content')}

        result = await _call([{'id': 'f1', 'name': 'small.txt'}], file_db)

        assert result.truncation_events == []


# ── Preamble idempotence tests ───────────────────────────────────────────────


class TestPreambleIdempotence:
    """Verify preamble_needed flag respects existing system messages."""

    @pytest.mark.asyncio
    async def test_preamble_needed_true_when_no_system_msg(self):
        file_db = {'f1': make_file_obj('doc.txt', content='Content')}
        messages = [{'role': 'user', 'content': 'Query'}]

        result = await _call(
            [{'id': 'f1', 'name': 'doc.txt'}],
            file_db,
            messages=messages,
        )

        assert result.preamble_needed is True

    @pytest.mark.asyncio
    async def test_preamble_needed_false_when_already_present(self):
        """If preamble is already the first system message, preamble_needed = False."""
        file_db = {'f1': make_file_obj('doc.txt', content='Content')}
        messages = [
            {'role': 'system', 'content': _SKIP_RAG_PREAMBLE},
            {'role': 'user', 'content': 'Query'},
        ]

        result = await _call(
            [{'id': 'f1', 'name': 'doc.txt'}],
            file_db,
            messages=messages,
        )

        assert result.preamble_needed is False

    @pytest.mark.asyncio
    async def test_preamble_needed_true_when_different_system_msg(self):
        """Non-preamble system message: preamble_needed = True."""
        file_db = {'f1': make_file_obj('doc.txt', content='Content')}
        messages = [
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Query'},
        ]

        result = await _call(
            [{'id': 'f1', 'name': 'doc.txt'}],
            file_db,
            messages=messages,
        )

        assert result.preamble_needed is True

    @pytest.mark.asyncio
    async def test_preamble_needed_false_when_no_content_produced(self):
        """No content → preamble_needed = False (nothing to inject)."""
        file_db = {'f1': make_file_obj('empty.txt', content='')}

        result = await _call([{'id': 'f1', 'name': 'empty.txt'}], file_db)

        assert result.preamble_needed is False
        assert result.context_block == ''


# ── Double-injection guard tests ─────────────────────────────────────────────


class TestDoubleInjectionGuard:
    """Verify double-injection raises RuntimeError."""

    @pytest.mark.asyncio
    async def test_double_injection_raises(self):
        """If BEGIN marker already in message list, RuntimeError is raised."""
        file_db = {'dup': make_file_obj('dup.txt', content='Some content')}
        poisoned_messages = [
            {'role': 'system', 'content': '<<FILE file-dup BEGIN>>\nalready here\n<<FILE file-dup END>>'},
            {'role': 'user', 'content': 'Query'},
        ]

        with pytest.raises(RuntimeError, match='double-injection'):
            await _call(
                [{'id': 'dup', 'name': 'dup.txt'}],
                file_db,
                messages=poisoned_messages,
            )


# ── Mixed scenario tests ─────────────────────────────────────────────────────


class TestMixedScenarios:
    """Combinations of docling and plain text files."""

    @pytest.mark.asyncio
    async def test_mixed_pdf_and_markdown(self):
        file_db = {
            'f1': make_file_obj('report.pdf', path='/uploads/report.pdf'),
            'f2': make_file_obj('notes.md', content='# Notes'),
        }
        mock_docling = AsyncMock(return_value='# PDF Content from docling')

        result = await _call(
            [
                {'id': 'f1', 'name': 'report.pdf'},
                {'id': 'f2', 'name': 'notes.md'},
            ],
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        assert '# PDF Content from docling' in result.context_block
        assert '# Notes' in result.context_block
        assert len(result.sources) == 2
        assert result.double_injection_applied is True

    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid(self):
        """Docling failure + missing file + valid markdown = only markdown survives."""
        file_db = {
            'f1': make_file_obj('broken.pdf', path='/uploads/broken.pdf'),
            'f3': make_file_obj('good.md', content='Good content'),
        }
        mock_docling = AsyncMock(side_effect=RuntimeError('docling failed'))

        result = await _call(
            [
                {'id': 'f1', 'name': 'broken.pdf'},
                {'id': 'missing'},
                {'id': 'f3', 'name': 'good.md'},
            ],
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        assert 'Good content' in result.context_block
        assert '<<FILE file-f3 BEGIN>>' in result.context_block
        assert '<<FILE file-f1' not in result.context_block
        assert len(result.sources) == 1

    @pytest.mark.asyncio
    async def test_no_id_skipped(self):
        file_db = {}

        result = await _call([{'name': 'no-id.md'}], file_db)

        assert result.context_block == ''
        assert result.sources == []

    @pytest.mark.asyncio
    async def test_empty_file_list(self):
        result = await _call([], {})

        assert result.context_block == ''
        assert result.sources == []
        assert result.preamble_needed is False
        assert result.truncation_events == []
        assert result.double_injection_applied is False
        assert result.server_side_filename_used is False

    @pytest.mark.asyncio
    async def test_filename_fallback_to_file_obj(self):
        """When item has no 'name', falls back to file_obj.filename for display."""
        file_db = {'f1': make_file_obj('from-db.md', content='Fallback name test')}

        result = await _call([{'id': 'f1'}], file_db)  # no 'name' key

        # source name should come from server-side filename
        assert len(result.sources) == 1
        assert result.sources[0]['source']['name'] == 'from-db.md'


# ── Source shape tests ───────────────────────────────────────────────────────


class TestSourceShape:
    """Verify the source dict shape matches what the citation UI expects."""

    @pytest.mark.asyncio
    async def test_source_has_required_keys(self):
        file_db = {'abc': make_file_obj('test.pdf', docling_md='Test content', path='/uploads/test.pdf')}

        result = await _call(
            [{'id': 'abc', 'name': 'test.pdf'}],
            file_db,
            docling_fn=AsyncMock(return_value='ignored — cached'),
        )

        src = result.sources[0]
        assert 'source' in src
        assert 'name' in src['source']
        assert 'id' in src['source']
        assert 'type' in src['source']
        assert 'document' in src
        assert isinstance(src['document'], list)
        assert 'metadata' in src
        assert isinstance(src['metadata'], list)
        assert 'file_id' in src['metadata'][0]
        assert 'name' in src['metadata'][0]
        assert 'source' in src['metadata'][0]

    @pytest.mark.asyncio
    async def test_source_passes_citation_filter(self):
        """Sources must pass the citation filter (name or id truthy)."""
        file_db = {'f1': make_file_obj('doc.md', content='Content')}

        result = await _call([{'id': 'f1', 'name': 'doc.md'}], file_db)

        filtered = [
            s for s in result.sources if s.get('source', {}).get('name', '') or s.get('source', {}).get('id', '')
        ]
        assert len(filtered) == 1


# ── Anti-spoofing: server-side filename for extension resolution ──────────────


class TestServerSideFilenameAntiSpoofing:
    """Verify server-side filename is used for extension routing (anti-spoofing)."""

    @pytest.mark.asyncio
    async def test_skip_rag_respects_server_side_filename_for_extension(self):
        """Client supplies .txt, server has .pdf → docling runs; server_side_filename_used=True.

        This test MUST fail if server-vs-client precedence were inverted:
          - If client name were used for extension routing, ext == '.txt' → NOT in DOCLING_EXTS
            → docling would NOT be called → mock_docling.assert_awaited_once() would FAIL.
        """
        # Server filename: .pdf (docling territory)
        # Client name:     .txt (plain text territory — would skip docling)
        file_db = {
            'spooftest': make_file_obj(
                'secret.pdf',  # server-side: PDF
                path='/uploads/secret.pdf',
            )
        }
        mock_docling = AsyncMock(return_value='# Docling output from server-side PDF')

        result = await _call(
            [{'id': 'spooftest', 'name': 'innocent.txt'}],  # client claims .txt
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        # Server-side filename override was active
        assert result.server_side_filename_used is True, (
            'server_side_filename_used must be True when server ext (.pdf) != client ext (.txt)'
        )

        # Docling MUST have run (server-side .pdf was used for routing)
        mock_docling.assert_awaited_once()

        # Context block must contain the docling output (server-side truth prevailed)
        assert '# Docling output from server-side PDF' in result.context_block, (
            'context_block must reflect server-side file processing (docling), not client claim'
        )

        # The delimited block must be present
        assert '<<FILE file-spooftest BEGIN>>' in result.context_block

    @pytest.mark.asyncio
    async def test_server_side_filename_used_false_when_extensions_match(self):
        """When client and server agree on extension, server_side_filename_used = False."""
        file_db = {'f1': make_file_obj('doc.pdf', path='/uploads/doc.pdf')}
        mock_docling = AsyncMock(return_value='# PDF output')

        result = await _call(
            [{'id': 'f1', 'name': 'doc.pdf'}],  # client claims same .pdf
            file_db,
            docling_fn=mock_docling,
            storage_fn=lambda p: p,
        )

        # Extensions match → no override signal
        assert result.server_side_filename_used is False

    @pytest.mark.asyncio
    async def test_client_pdf_server_txt_uses_server_txt_path(self):
        """Client claims .pdf, server has .txt → plain text path used (no docling).

        Inversion check: if client extension were used, ext == '.pdf' → docling would
        be called → mock_docling.assert_not_awaited() would FAIL.
        """
        # Server has a plain text file; client tries to claim it's a PDF
        file_db = {
            'f1': make_file_obj(
                'plain.txt',  # server-side: plain text
                content='Plain text content',
            )
        }
        # docling should NOT be called because server-side ext is .txt
        mock_docling = AsyncMock(side_effect=AssertionError('docling must not be called for server .txt'))

        result = await _call(
            [{'id': 'f1', 'name': 'claim.pdf'}],  # client lies about extension
            file_db,
            docling_fn=mock_docling,
        )

        mock_docling.assert_not_awaited()

        # server_side_filename_used is True because extensions differ
        assert result.server_side_filename_used is True

        # Plain text content in context block
        assert 'Plain text content' in result.context_block

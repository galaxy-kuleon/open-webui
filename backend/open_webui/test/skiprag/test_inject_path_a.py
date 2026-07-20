import asyncio
import sys
from types import SimpleNamespace

import pytest
from open_webui.skiprag import convert, inject
from open_webui.skiprag.convert import ConversionOutcome
from open_webui.utils.file_coverage import assert_warning_privacy_safe, build_warning_payload


def _body():
    return {
        'metadata': {
            'files': [
                {'type': 'file', 'id': 'file-1', 'name': 'same.pdf'},
                {'type': 'file', 'id': 'file-2', 'name': 'same.pdf'},
                {'type': 'collection', 'id': 'keep-me'},
            ],
        },
        'messages': [{'role': 'user', 'content': 'summarize the attachment'}],
    }


def test_duplicate_file_ids_convert_once_and_success_is_injected_once(monkeypatch):
    calls = []

    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(b'identical original PDF bytes')

    def fake_convert(raw, filename, request=None):
        calls.append((raw, filename))
        return ConversionOutcome.complete('# extracted')

    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', fake_convert)

    body, _ = asyncio.run(inject.run_skip_rag_path_a(None, _body(), {}, SimpleNamespace(id='user-1')))

    assert calls == [(b'identical original PDF bytes', 'same.pdf')]
    assert body['messages'][0]['content'].count('# extracted') == 1
    assert body['metadata']['files'] == [{'type': 'collection', 'id': 'keep-me'}]
    assert 'skip_rag_materials_warning' not in body['metadata']


def test_duplicate_unavailable_outcome_converts_once_and_surfaces_marker_and_warning(monkeypatch):
    calls = []

    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(b'identical original PDF bytes')

    def fake_convert(raw, filename, request=None):
        calls.append((raw, filename))
        return ConversionOutcome.unavailable('docling_request_failed')

    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', fake_convert)

    body, _ = asyncio.run(inject.run_skip_rag_path_a(None, _body(), {}, SimpleNamespace(id='user-1')))

    assert len(calls) == 1
    assert body['metadata']['files'] == [{'type': 'collection', 'id': 'keep-me'}]
    content = body['messages'][0]['content']
    assert 'status="unavailable"' in content
    assert 'Do not claim' in content
    assert 'conversion-job metadata is not document content' in content
    assert 'same.pdf' not in content
    counts = body['metadata']['skip_rag_materials_warning']
    assert counts == {'used': 0, 'total': 2, 'unused': 2, 'skipped': 0, 'failed': 2}
    payload = build_warning_payload('chat-1', 'message-1', **counts)
    assert assert_warning_privacy_safe(payload) == payload


def test_mixed_complete_and_unavailable_materials_surface_both_content_and_warning(
    monkeypatch,
):
    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(filename.encode('utf-8'))

    def fake_convert(raw, filename, request=None):
        if filename == 'readable.pdf':
            return ConversionOutcome.complete('# readable content')
        return ConversionOutcome.unavailable('docling_request_failed')

    body = _body()
    body['metadata']['files'] = [
        {'type': 'file', 'id': 'readable', 'name': 'readable.pdf'},
        {'type': 'file', 'id': 'unavailable', 'name': 'unavailable.pdf'},
    ]
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', fake_convert)

    body, _ = asyncio.run(
        inject.run_skip_rag_path_a(None, body, {}, SimpleNamespace(id='user-1'))
    )

    content = body['messages'][0]['content']
    assert content.count('<source ') == 1
    assert '# readable content' in content
    assert 'status="unavailable" total="1"' in content
    assert body['metadata']['skip_rag_materials_warning'] == {
        'used': 1,
        'total': 2,
        'unused': 1,
        'skipped': 0,
        'failed': 1,
    }


def test_same_content_and_extension_with_different_names_dedupes_once(monkeypatch):
    calls = []

    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(b'identical bytes')

    def fake_convert(raw, filename, request=None):
        calls.append(filename)
        return ConversionOutcome.complete('# one source')

    body = _body()
    body['metadata']['files'] = [
        {'type': 'file', 'id': 'one', 'name': 'first.pdf'},
        {'type': 'file', 'id': 'two', 'name': 'second.pdf'},
    ]
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', fake_convert)

    body, _ = asyncio.run(
        inject.run_skip_rag_path_a(None, body, {}, SimpleNamespace(id='user-1'))
    )

    assert calls == ['first.pdf']
    content = body['messages'][0]['content']
    assert content.count('<source ') == 1
    assert content.count('# one source') == 1
    assert 'name="first.pdf"' in content
    assert 'name="second.pdf"' not in content


def test_different_content_with_same_name_does_not_dedupe(monkeypatch):
    calls = []

    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(file_item['id'].encode('utf-8'))

    def fake_convert(raw, filename, request=None):
        calls.append(raw)
        return ConversionOutcome.complete(f'# source {raw.decode("utf-8")}')

    body = _body()
    body['metadata']['files'] = [
        {'type': 'file', 'id': 'one', 'name': 'same.pdf'},
        {'type': 'file', 'id': 'two', 'name': 'same.pdf'},
    ]
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', fake_convert)

    body, _ = asyncio.run(
        inject.run_skip_rag_path_a(None, body, {}, SimpleNamespace(id='user-1'))
    )

    assert calls == [b'one', b'two']
    content = body['messages'][0]['content']
    assert content.count('<source ') == 2
    assert '# source one' in content
    assert '# source two' in content


def test_unexpected_pdf_exception_reaches_path_a_marker_and_warning(
    monkeypatch,
    tmp_path,
):
    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(b'%PDF unexpected')

    def raise_memory_error(*args, **kwargs):
        raise MemoryError('simulated allocation failure')

    body = _body()
    body['metadata']['files'] = body['metadata']['files'][:1]
    monkeypatch.setattr(convert, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(convert, '_docling_pdf_outcome', raise_memory_error)
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)

    body, _ = asyncio.run(
        inject.run_skip_rag_path_a(None, body, {}, SimpleNamespace(id='user-1'))
    )

    assert 'status="unavailable" total="1"' in body['messages'][0]['content']
    assert body['metadata']['skip_rag_materials_warning'] == {
        'used': 0,
        'total': 1,
        'unused': 1,
        'skipped': 0,
        'failed': 1,
    }
    assert list(tmp_path.iterdir()) == []


def test_prepared_db_markdown_keeps_existing_direct_injection_behavior(monkeypatch):
    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(
            raw_bytes=b'already extracted text',
            prepared_markdown='already extracted text',
        )

    def unexpected_convert(*args, **kwargs):
        raise AssertionError('prepared DB markdown must not be reconverted')

    body = _body()
    body['metadata']['files'] = body['metadata']['files'][:1]
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'convert_to_markdown_outcome', unexpected_convert)

    body, _ = asyncio.run(inject.run_skip_rag_path_a(None, body, {}, SimpleNamespace(id='user-1')))

    assert 'already extracted text' in body['messages'][0]['content']
    assert 'skip_rag_materials_warning' not in body['metadata']


def test_unavailable_attachment_is_not_removed_when_no_user_message_can_receive_marker(
    monkeypatch,
):
    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(b'failed PDF bytes')

    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(
        inject,
        'convert_to_markdown_outcome',
        lambda *args: ConversionOutcome.unavailable('docling_request_failed'),
    )
    body = _body()
    body['messages'] = [{'role': 'assistant', 'content': 'existing'}]

    with pytest.raises(RuntimeError, match='requires a user message'):
        asyncio.run(
            inject.run_skip_rag_path_a(
                None,
                body,
                {},
                SimpleNamespace(id='user-1'),
            )
        )

    assert len(body['metadata']['files']) == 3
    assert body['metadata']['skip_rag_materials_warning']['unused'] == 2


def test_phase4_cardinality_mismatch_fails_closed(monkeypatch):
    async def fake_resolve(file_item, filename):
        return inject.ResolvedMaterial(
            raw_bytes=file_item['id'].encode('utf-8'),
            prepared_markdown='large prepared material ' * 100,
        )

    async def wrong_cardinality(items, query, target_tokens, request=None):
        return [items[0]]

    body = _body()
    body['metadata']['files'] = [
        {'type': 'file', 'id': 'one', 'name': 'one.txt'},
        {'type': 'file', 'id': 'two', 'name': 'two.txt'},
    ]
    monkeypatch.setattr(inject, '_resolve_file_bytes', fake_resolve)
    monkeypatch.setattr(inject, 'TRIGGER_TOKENS', 0)
    monkeypatch.setattr(inject, 'extract_to_budget', wrong_cardinality)
    monkeypatch.setitem(
        sys.modules,
        'open_webui.utils.misc',
        SimpleNamespace(get_last_user_message=lambda _: 'summarize materials'),
    )

    with pytest.raises(RuntimeError, match='cardinality mismatch'):
        asyncio.run(
            inject.run_skip_rag_path_a(
                None,
                body,
                {},
                SimpleNamespace(id='user-1'),
            )
        )

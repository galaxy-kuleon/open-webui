import inspect
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from open_webui.skiprag import convert


class FakeResponse:
    def __init__(self, payload=None, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture(autouse=True)
def isolate_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(convert, 'CACHE_DIR', str(tmp_path))


def _markdown_response(text):
    return FakeResponse({'document': {'md_content': text}})


def test_plan_pdf_ranges_covers_58_pages_exactly_once():
    ranges = convert._plan_pdf_ranges(58, 4)

    assert ranges == [
        (1, 4),
        (5, 8),
        (9, 12),
        (13, 16),
        (17, 20),
        (21, 24),
        (25, 28),
        (29, 32),
        (33, 36),
        (37, 40),
        (41, 44),
        (45, 48),
        (49, 52),
        (53, 56),
        (57, 58),
    ]
    assert [page for start, end in ranges for page in range(start, end + 1)] == list(range(1, 59))


@pytest.mark.parametrize(
    ('pages', 'chunk_pages', 'expected'),
    [
        (1, 4, [(1, 1)]),
        (4, 4, [(1, 4)]),
        (5, 4, [(1, 4), (5, 5)]),
        (8, 4, [(1, 4), (5, 8)]),
        (9, 4, [(1, 4), (5, 8), (9, 9)]),
    ],
)
def test_plan_pdf_ranges_boundaries(pages, chunk_pages, expected):
    assert convert._plan_pdf_ranges(pages, chunk_pages) == expected


def test_pdf_ranges_reuse_original_bytes_and_send_repeated_multipart_fields(monkeypatch):
    raw = b'%PDF original bytes must be reused'
    calls = []
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 5)
    monkeypatch.setattr(convert, 'PDF_CHUNK_PAGES', 4)

    def fake_post(url, files, data, timeout):
        calls.append({'url': url, 'files': files, 'data': data, 'timeout': timeout})
        page_range = [value for key, value in data if key == 'page_range']
        return _markdown_response(f'range-{page_range[0]}-{page_range[1]}')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    outcome = convert.convert_to_markdown_outcome(raw, 'scan.pdf')

    assert outcome.is_complete
    assert [call['files']['files'][1] for call in calls] == [raw, raw]
    assert [[value for key, value in call['data'] if key == 'page_range'] for call in calls] == [
        [1, 4],
        [5, 5],
    ]
    for call in calls:
        assert ('to_formats', 'md') in call['data']
        assert ('image_export_mode', 'placeholder') in call['data']
        assert ('md_page_break_placeholder', convert.MARKDOWN_PAGE_BREAK) in call['data']
    assert calls[0]['timeout'] == convert.OCR_PAGE_TIMEOUT_SECONDS * 4
    assert calls[1]['timeout'] == convert.OCR_PAGE_TIMEOUT_SECONDS
    assert outcome.markdown == f'range-1-4\n\n{convert.MARKDOWN_PAGE_BREAK}\n\nrange-5-5'
    assert 'PdfWriter' not in inspect.getsource(convert)


def test_small_pdf_keeps_one_request_without_page_range(monkeypatch):
    calls = []
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 4)
    monkeypatch.setattr(convert, 'PDF_CHUNK_PAGES', 4)

    def fake_post(url, files, data, timeout):
        calls.append((data, timeout))
        return _markdown_response('small')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    outcome = convert.convert_to_markdown_outcome(b'%PDF small', 'small.pdf')

    assert outcome.is_complete
    assert len(calls) == 1
    assert [item for item in calls[0][0] if item[0] == 'page_range'] == []
    assert calls[0][1] == convert.OCR_PAGE_TIMEOUT_SECONDS * 4


def test_small_pdf_timeout_uses_actual_pages_not_chunk_size(monkeypatch):
    calls = []
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 2)
    monkeypatch.setattr(convert, 'PDF_CHUNK_PAGES', 4)

    def fake_post(url, files, data, timeout):
        calls.append(timeout)
        return _markdown_response('small')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    outcome = convert.convert_to_markdown_outcome(b'%PDF two pages', 'small.pdf')

    assert outcome.is_complete
    assert calls == [convert.OCR_PAGE_TIMEOUT_SECONDS * 2]


def test_non_pdf_docling_multipart_remains_compatible(monkeypatch):
    calls = []

    def fake_post(url, files, data, timeout):
        calls.append(data)
        return _markdown_response('office markdown')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    assert convert._docling_convert(b'OOXML bytes', 'sample.docx', 'docx') == 'office markdown'
    assert calls == [
        [
            ('to_formats', 'md'),
            ('image_export_mode', 'placeholder'),
        ]
    ]


def test_non_pdf_empty_markdown_keeps_legacy_empty_string_behavior(monkeypatch):
    monkeypatch.setattr(
        convert.requests,
        'post',
        lambda **kwargs: FakeResponse({'document': {'md_content': ''}}),
    )

    assert convert._docling_convert(b'OOXML bytes', 'sample.docx', 'docx') == ''


def test_pdf_page_count_failure_is_typed_unavailable_without_unbounded_post(monkeypatch):
    def fail_count(_):
        raise convert.PDFPageCountError('cannot count')

    monkeypatch.setattr(convert, '_count_pdf_pages', fail_count)
    monkeypatch.setattr(
        convert.requests,
        'post',
        lambda **kwargs: pytest.fail('must not submit an unbounded whole PDF'),
    )

    outcome = convert.convert_to_markdown_outcome(b'not a PDF', 'broken.pdf')

    assert outcome.is_unavailable
    assert outcome.failure_kind == 'pdf_page_count_failed'


@pytest.mark.parametrize(
    'response',
    [
        FakeResponse(status_error=convert.requests.ConnectionError('disconnect')),
        FakeResponse(status_error=convert.requests.HTTPError('500')),
        FakeResponse(status_error=convert.requests.Timeout('range timeout')),
        FakeResponse(ValueError('malformed')),
        FakeResponse({'document': {'md_content': ''}}),
        FakeResponse({'unexpected': 'shape'}),
    ],
)
def test_any_failed_range_makes_whole_pdf_unavailable_and_not_cached(monkeypatch, tmp_path, response):
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 5)
    monkeypatch.setattr(convert, 'PDF_CHUNK_PAGES', 4)
    responses = iter([_markdown_response('first range'), response])
    monkeypatch.setattr(convert.requests, 'post', lambda **kwargs: next(responses))

    outcome = convert.convert_to_markdown_outcome(b'%PDF failure', 'scan.pdf')

    assert outcome.is_unavailable
    assert outcome.markdown == ''
    assert outcome.failure_kind in {
        'docling_request_failed',
        'docling_timeout',
        'docling_malformed_response',
        'docling_empty_markdown',
    }
    assert list(tmp_path.iterdir()) == []


def test_public_string_wrapper_returns_empty_for_unavailable(monkeypatch):
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 1)
    monkeypatch.setattr(
        convert.requests,
        'post',
        lambda **kwargs: FakeResponse({'document': {'md_content': ''}}),
    )

    assert convert.convert_to_markdown(b'%PDF failure', 'scan.pdf') == ''


def test_unexpected_pdf_exception_is_typed_unavailable_and_not_cached(
    monkeypatch,
    tmp_path,
    caplog,
):
    def raise_memory_error(*args, **kwargs):
        raise MemoryError('simulated allocation failure')

    monkeypatch.setattr(convert, '_docling_pdf_outcome', raise_memory_error)

    with caplog.at_level(logging.WARNING, logger=convert.__name__):
        outcome = convert.convert_to_markdown_outcome(b'%PDF unexpected', 'scan.pdf')

    assert outcome.is_unavailable
    assert outcome.failure_kind == 'unexpected_error'
    assert outcome.markdown == ''
    assert list(tmp_path.iterdir()) == []
    events = [
        json.loads(record.message.removeprefix('skip-rag: docling_pdf_unexpected '))
        for record in caplog.records
        if record.message.startswith('skip-rag: docling_pdf_unexpected ')
    ]
    assert events == [
        {
            'failure_kind': 'unexpected_error',
            'outcome': 'unavailable',
        }
    ]
    assert 'simulated allocation failure' not in caplog.text


def test_complete_pdf_cache_is_reused_across_calls(monkeypatch, tmp_path):
    calls = []
    raw = b'%PDF cache reuse'
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 1)

    def fake_post(**kwargs):
        calls.append(kwargs)
        return _markdown_response('complete markdown')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    first = convert.convert_to_markdown_outcome(raw, 'scan.pdf')
    second = convert.convert_to_markdown_outcome(raw, 'scan.pdf')

    assert first.is_complete and second.is_complete
    assert first.markdown == second.markdown == 'complete markdown'
    assert len(calls) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_pdf_range_logs_are_structured_and_privacy_bounded(monkeypatch, caplog):
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 5)
    monkeypatch.setattr(convert, 'PDF_CHUNK_PAGES', 4)
    monkeypatch.setattr(
        convert.requests,
        'post',
        lambda **kwargs: _markdown_response('range markdown'),
    )

    with caplog.at_level(logging.INFO, logger=convert.__name__):
        outcome = convert.convert_to_markdown_outcome(b'%PDF logged', 'private-name.pdf')

    assert outcome.is_complete
    range_events = [
        json.loads(record.message.removeprefix('skip-rag: docling_pdf_range '))
        for record in caplog.records
        if record.message.startswith('skip-rag: docling_pdf_range ')
    ]
    assert [(event['range_start'], event['range_end']) for event in range_events] == [
        (1, 4),
        (5, 5),
    ]
    assert all(event['page_count'] == 5 for event in range_events)
    assert all(event['range_total'] == 2 for event in range_events)
    assert all(event['outcome'] == 'complete' for event in range_events)
    assert all(
        len(event['hash_prefix']) == convert.LOG_HASH_PREFIX_LENGTH
        for event in range_events
    )
    assert 'private-name.pdf' not in caplog.text
    assert '"cache_decision": "write_complete"' in caplog.text


def test_pdf_runtime_defaults_are_named_and_measured(monkeypatch):
    monkeypatch.delenv('TEST_PDF_CHUNK_PAGES', raising=False)
    monkeypatch.delenv('TEST_DOCLING_MAX_INFLIGHT', raising=False)

    assert convert.DEFAULT_PDF_CHUNK_PAGES == 4
    assert convert.DEFAULT_DOCLING_MAX_INFLIGHT == 1
    assert convert.DEFAULT_OCR_PAGE_TIMEOUT_SECONDS == 900
    assert convert.DEFAULT_DOCLING_NON_PDF_TIMEOUT_SECONDS == 1800
    assert convert.DEFAULT_SOFFICE_TIMEOUT_SECONDS == 1800
    assert (
        convert._read_positive_int_env(
            'TEST_PDF_CHUNK_PAGES',
            convert.DEFAULT_PDF_CHUNK_PAGES,
        )
        == 4
    )
    assert (
        convert._read_positive_int_env(
            'TEST_DOCLING_MAX_INFLIGHT',
            convert.DEFAULT_DOCLING_MAX_INFLIGHT,
        )
        == 1
    )


def test_invalid_positive_integer_config_fails_loudly(monkeypatch):
    for raw in ('0', '-1', 'abc', ''):
        monkeypatch.setenv('TEST_POSITIVE_INT', raw)
        with pytest.raises(ValueError, match='TEST_POSITIVE_INT'):
            convert._read_positive_int_env('TEST_POSITIVE_INT', 4)


@pytest.mark.parametrize(
    'name',
    [
        'SKIP_RAG_PDF_CHUNK_PAGES',
        'SKIP_RAG_DOCLING_MAX_INFLIGHT',
        'SKIP_RAG_OCR_PAGE_TIMEOUT',
        'SKIP_RAG_DOCLING_NON_PDF_TIMEOUT_SECONDS',
        'SKIP_RAG_SOFFICE_TIMEOUT_SECONDS',
    ],
)
def test_invalid_runtime_config_fails_module_import_loudly(name):
    env = os.environ.copy()
    env[name] = '0'

    result = subprocess.run(
        [sys.executable, '-c', 'import open_webui.skiprag.convert'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert name in result.stderr
    assert 'must be an integer >= 1' in result.stderr


def test_process_wide_semaphore_bounds_concurrent_docling_calls(monkeypatch):
    current = 0
    peak = 0
    lock = threading.Lock()
    monkeypatch.setattr(convert, '_count_pdf_pages', lambda _: 1)
    monkeypatch.setattr(convert, '_DOCLING_SEMAPHORE', threading.BoundedSemaphore(1))

    def fake_post(url, files, data, timeout):
        nonlocal current, peak
        with lock:
            current += 1
            peak = max(peak, current)
        time.sleep(0.05)
        with lock:
            current -= 1
        return _markdown_response('ok')

    monkeypatch.setattr(convert.requests, 'post', fake_post)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda n: convert.convert_to_markdown_outcome(f'%PDF concurrent {n}'.encode(), f'{n}.pdf'),
                range(2),
            )
        )

    assert all(outcome.is_complete for outcome in outcomes)
    assert peak == 1

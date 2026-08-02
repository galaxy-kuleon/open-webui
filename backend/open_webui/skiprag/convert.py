"""
skip-rag Phase 3 — file → markdown conversion module.

Public API
----------
convert_to_markdown(file_bytes_or_path, filename, request=None) -> str
    Convert a file to markdown.  Route:
      - .md / .txt           → read text directly (no docling)
      - .eml                 → dedicated email parser (no docling)
      - legacy .doc/.xls/.ppt → POST soffice → modern bytes → docling
      - modern .docx/.xlsx/.pptx + .pdf + images → docling directly
    Cache: keyed by sha256(original bytes) + ext; stored under
    SKIP_RAG_CACHE_DIR (default /app/backend/skiprag-cache).
    Non-PDF fallback: on a soffice/docling failure, use OWUI's Loader chain.
    PDFs fail closed when page counting or any native page range is unavailable.
    The public string API returns '' for an unavailable conversion.

count_tokens(text) -> int
    Tiktoken token count using TIKTOKEN_ENCODING_NAME env var.

extract_to_budget(items, query, target_tokens) -> list[str]
    Phase 3 STUB (proportional truncation only).  This stub is kept for
    backwards-compat but the Phase 4 real implementation lives in extract.py
    (async, extractor-model-aware).  inject.py imports from extract.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import requests
import tiktoken

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------


def _read_positive_int_env(name: str, default: int) -> int:
    """Read a positive integer setting and fail loudly on invalid deployment config."""
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must be an integer >= 1, got {raw!r}') from exc
    if value < 1:
        raise ValueError(f'{name} must be an integer >= 1, got {raw!r}')
    return value


DOCLING_SERVER_URL: str = os.environ.get('DOCLING_SERVER_URL', 'http://docling:5001').rstrip('/')
SOFFICE_URL: str = os.environ.get('SKIP_RAG_SOFFICE_URL', 'http://soffice:2004').rstrip('/')
CACHE_DIR: str = os.environ.get('SKIP_RAG_CACHE_DIR', '/app/backend/skiprag-cache')
DEFAULT_OCR_PAGE_TIMEOUT_SECONDS: int = 240
DEFAULT_PDF_CHUNK_PAGES: int = 4
DEFAULT_DOCLING_MAX_INFLIGHT: int = 1
# Maximum seconds allowed per page in the current Docling PDF range.
OCR_PAGE_TIMEOUT_SECONDS: int = _read_positive_int_env(
    'SKIP_RAG_OCR_PAGE_TIMEOUT',
    DEFAULT_OCR_PAGE_TIMEOUT_SECONDS,
)
# Maximum PDF pages submitted in one native Docling page_range request.
PDF_CHUNK_PAGES: int = _read_positive_int_env(
    'SKIP_RAG_PDF_CHUNK_PAGES',
    DEFAULT_PDF_CHUNK_PAGES,
)
# Maximum process-wide concurrent Docling HTTP requests across worker threads.
DOCLING_MAX_INFLIGHT: int = _read_positive_int_env(
    'SKIP_RAG_DOCLING_MAX_INFLIGHT',
    DEFAULT_DOCLING_MAX_INFLIGHT,
)
DOCLING_NON_PDF_TIMEOUT_SECONDS: int = 120
SOFFICE_TIMEOUT_SECONDS: int = 120
LOG_HASH_PREFIX_LENGTH: int = 16
ELAPSED_SECONDS_DECIMAL_PLACES: int = 3
# Explicit boundary emitted within a range by Docling and between adjacent ranges by OWUI.
MARKDOWN_PAGE_BREAK: str = '<!-- page-break -->'
_DOCLING_SEMAPHORE = threading.BoundedSemaphore(DOCLING_MAX_INFLIGHT)
# Backward-compatible constant name for existing imports/config documentation.
OCR_PAGE_TIMEOUT: int = OCR_PAGE_TIMEOUT_SECONDS
TRIGGER_TOKENS: int = int(os.environ.get('SKIP_RAG_TRIGGER_TOKENS', '16000'))
TARGET_TOKENS: int = int(os.environ.get('SKIP_RAG_TARGET_TOKENS', '12000'))
TIKTOKEN_ENCODING: str = os.environ.get('TIKTOKEN_ENCODING_NAME', 'cl100k_base')

# Legacy → modern mapping for soffice conversion
LEGACY_EXT_TO_TARGET: dict[str, str] = {
    'doc': 'docx',
    'xls': 'xlsx',
    'ppt': 'pptx',
}

# Extensions handled directly by docling (no soffice step)
DOCLING_DIRECT_EXTS: frozenset[str] = frozenset({
    'docx', 'xlsx', 'pptx', 'pdf',
    'png', 'jpg', 'jpeg', 'gif', 'tiff', 'webp', 'bmp',
})

# Extensions handled by the dedicated email parser before docling/fallback
EMAIL_EXTS: frozenset[str] = frozenset({'eml'})

# Outlook compound-file email. Docling has no CFBF reader, so every .msg used
# to fall through to the unknown-extension branch, spend ~2s on a doomed
# docling round-trip and come back empty (`empty_conversion`). Handled by the
# stdlib CFBF reader in skiprag/msg.py instead. A malformed .msg still reaches
# the caller's generic Loader fallback and surfaces as `fallback_empty`, which
# is a distinguishable failure kind rather than the old silent docling miss.
MSG_EXTS: frozenset[str] = frozenset({'msg'})

# Extensions that are just plain text — read directly
PLAINTEXT_EXTS: frozenset[str] = frozenset({'md', 'txt'})


class ConversionStatus(StrEnum):
    COMPLETE = 'complete'
    UNAVAILABLE = 'unavailable'


@dataclass(frozen=True)
class ConversionOutcome:
    """Internal result contract; public ``convert_to_markdown`` remains string-compatible."""

    status: ConversionStatus
    markdown: str = ''
    failure_kind: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.status is ConversionStatus.COMPLETE

    @property
    def is_unavailable(self) -> bool:
        return self.status is ConversionStatus.UNAVAILABLE

    @classmethod
    def complete(cls, markdown: str) -> ConversionOutcome:
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError('complete conversion outcome requires non-empty markdown')
        return cls(status=ConversionStatus.COMPLETE, markdown=markdown)

    @classmethod
    def unavailable(cls, failure_kind: str) -> ConversionOutcome:
        return cls(status=ConversionStatus.UNAVAILABLE, failure_kind=failure_kind)


class PDFPageCountError(ValueError):
    """The PDF cannot be safely assigned to bounded native page ranges."""


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(raw_bytes: bytes, ext: str) -> str:
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if ext == 'eml':
        return f'{digest}.{ext}.attach-v1.md'
    return f'{digest}.{ext}.md'


def content_dedupe_key(raw_bytes: bytes, filename: str) -> tuple[str, str]:
    """Same-turn identity: full content SHA-256 plus normalized extension."""
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return hashlib.sha256(raw_bytes).hexdigest(), ext


def _cache_read(key: str) -> str | None:
    path = Path(CACHE_DIR) / key
    if path.exists():
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            return None
    return None


def _cache_write(key: str, text: str) -> bool:
    _ensure_cache_dir()
    path = Path(CACHE_DIR) / key
    try:
        path.write_text(text, encoding='utf-8')
        return True
    except OSError as exc:
        log.warning('skip-rag: cache write failed; kind=%s', type(exc).__name__)
        return False


# ---------------------------------------------------------------------------
# Soffice conversion
# ---------------------------------------------------------------------------
def _soffice_convert(file_bytes: bytes, filename: str, target_ext: str) -> bytes:
    """
    POST file bytes to the soffice sidecar and return the converted OOXML bytes.
    Raises requests.RequestException on failure.
    """
    resp = requests.post(
        f'{SOFFICE_URL}/convert',
        params={'to': target_ext},
        files={'file': (filename, file_bytes)},
        timeout=SOFFICE_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Docling conversion
# ---------------------------------------------------------------------------
def _count_pdf_pages(file_bytes: bytes) -> int:
    """Return a positive page count or fail closed before any unbounded request."""
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        page_count = len(reader.pages)
    except Exception as exc:
        raise PDFPageCountError('unable to count PDF pages safely') from exc
    if page_count < 1:
        raise PDFPageCountError('PDF must contain at least one page')
    return page_count


def _plan_pdf_ranges(page_count: int, chunk_pages: int) -> list[tuple[int, int]]:
    """Return strict 1-based inclusive coverage of every page exactly once."""
    if page_count < 1:
        raise ValueError(f'page_count must be >= 1, got {page_count!r}')
    if chunk_pages < 1:
        raise ValueError(f'chunk_pages must be >= 1, got {chunk_pages!r}')
    return [(start, min(start + chunk_pages - 1, page_count)) for start in range(1, page_count + 1, chunk_pages)]


def _docling_form_data(
    page_range: tuple[int, int] | None = None,
    *,
    include_page_break_placeholder: bool = False,
) -> list[tuple[str, object]]:
    data: list[tuple[str, object]] = [
        ('to_formats', 'md'),
        ('image_export_mode', 'placeholder'),
    ]
    if include_page_break_placeholder:
        data.append(('md_page_break_placeholder', MARKDOWN_PAGE_BREAK))
    if page_range is not None:
        data.extend((('page_range', page_range[0]), ('page_range', page_range[1])))
    return data


def _extract_docling_markdown(response) -> ConversionOutcome:
    try:
        result = response.json()
    except (TypeError, ValueError):
        return ConversionOutcome.unavailable('docling_malformed_response')
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return ConversionOutcome.unavailable('docling_malformed_response')
    doc = result.get('document', result)
    if not isinstance(doc, dict):
        return ConversionOutcome.unavailable('docling_malformed_response')
    md = doc.get('md_content') or doc.get('content', '')
    if not isinstance(md, str):
        return ConversionOutcome.unavailable('docling_malformed_response')
    if not md.strip():
        return ConversionOutcome.unavailable('docling_empty_markdown')
    return ConversionOutcome.complete(md)


def _docling_request(
    file_bytes: bytes,
    filename: str,
    *,
    timeout_seconds: int,
    page_range: tuple[int, int] | None = None,
    include_page_break_placeholder: bool = False,
) -> ConversionOutcome:
    try:
        with _DOCLING_SEMAPHORE:
            response = requests.post(
                url=f'{DOCLING_SERVER_URL}/v1/convert/file',
                files={'files': (filename, file_bytes, 'application/octet-stream')},
                data=_docling_form_data(
                    page_range,
                    include_page_break_placeholder=include_page_break_placeholder,
                ),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        return _extract_docling_markdown(response)
    except requests.Timeout:
        return ConversionOutcome.unavailable('docling_timeout')
    except requests.RequestException:
        return ConversionOutcome.unavailable('docling_request_failed')


def _docling_pdf_outcome(file_bytes: bytes, filename: str) -> ConversionOutcome:
    try:
        page_count = _count_pdf_pages(file_bytes)
    except PDFPageCountError:
        log.warning('skip-rag: docling_pdf_page_count outcome=unavailable')
        return ConversionOutcome.unavailable('pdf_page_count_failed')
    ranges = _plan_pdf_ranges(page_count, PDF_CHUNK_PAGES)
    use_native_ranges = page_count > PDF_CHUNK_PAGES
    hash_prefix = hashlib.sha256(file_bytes).hexdigest()[:LOG_HASH_PREFIX_LENGTH]
    markdown_ranges: list[str] = []

    for range_index, (start, end) in enumerate(ranges, start=1):
        pages_in_range = end - start + 1
        native_range = (start, end) if use_native_ranges else None
        started = time.monotonic()
        outcome = _docling_request(
            file_bytes,
            filename,
            timeout_seconds=OCR_PAGE_TIMEOUT_SECONDS * pages_in_range,
            page_range=native_range,
            include_page_break_placeholder=True,
        )
        elapsed_seconds = round(
            time.monotonic() - started,
            ELAPSED_SECONDS_DECIMAL_PLACES,
        )
        log.info(
            'skip-rag: docling_pdf_range %s',
            json.dumps(
                {
                    'hash_prefix': hash_prefix,
                    'page_count': page_count,
                    'range_start': start,
                    'range_end': end,
                    'range_index': range_index,
                    'range_total': len(ranges),
                    'elapsed_seconds': elapsed_seconds,
                    'outcome': outcome.status.value,
                    'failure_kind': outcome.failure_kind,
                },
                sort_keys=True,
            ),
        )
        if outcome.is_unavailable:
            return outcome
        markdown_ranges.append(outcome.markdown.strip())

    combined = f'\n\n{MARKDOWN_PAGE_BREAK}\n\n'.join(markdown_ranges)
    return ConversionOutcome.complete(combined)


def _docling_convert(file_bytes: bytes, filename: str, ext: str) -> str:
    """
    POST file bytes to docling-serve and return the markdown text.
    PDF timeout scales by pages in the current native page range.
    Raises requests.RequestException on failure.
    """
    if ext == 'pdf':
        outcome = _docling_pdf_outcome(file_bytes, filename)
    else:
        outcome = _docling_request(
            file_bytes,
            filename,
            timeout_seconds=DOCLING_NON_PDF_TIMEOUT_SECONDS,
        )
    if outcome.is_unavailable and not (
        ext != 'pdf' and outcome.failure_kind == 'docling_empty_markdown'
    ):
        raise requests.RequestException(outcome.failure_kind)
    return outcome.markdown


# ---------------------------------------------------------------------------
# Fallback via OWUI Loader chain
# ---------------------------------------------------------------------------
def _fallback_loader(file_bytes: bytes, filename: str, request=None) -> str:
    """
    Use OWUI's existing Loader chain to extract text when soffice/docling fails.
    Writes bytes to a temp file so Loader can open it.
    Returns extracted text (may be sparse / unformatted).
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    suffix = f'.{ext}' if ext else ''

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if request is not None:
            from open_webui.retrieval.utils import build_loader_from_config
            loader = build_loader_from_config(request)
        else:
            from open_webui.retrieval.loaders.main import Loader
            loader = Loader(engine='')

        loader.user = None
        docs = loader.load(filename, None, tmp_path)
        return '\n\n'.join(doc.page_content for doc in docs if doc.page_content)
    except Exception as exc:
        log.error(
            'skip-rag: fallback loader failed for extension %s; kind=%s',
            ext,
            type(exc).__name__,
        )
        return ''
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main conversion entry point
# ---------------------------------------------------------------------------
def _resolve_file_input(
    file_bytes_or_path: bytes | str | os.PathLike | None,
    file_bytes: bytes | None,
    filename: str,
) -> bytes | str | os.PathLike:
    if file_bytes is not None:
        return file_bytes
    if file_bytes_or_path is None:
        log.error('skip-rag: no file bytes or path provided')
        return b''
    return file_bytes_or_path


def convert_to_markdown(
    file_bytes_or_path: bytes | str | os.PathLike | None = None,
    filename: str = '',
    request=None,
    *,
    file_bytes: bytes | None = None,
) -> str:
    """Backward-compatible string API over the typed conversion outcome."""
    return convert_to_markdown_outcome(
        file_bytes_or_path,
        filename,
        request,
        file_bytes=file_bytes,
    ).markdown


def convert_to_markdown_outcome(
    file_bytes_or_path: bytes | str | os.PathLike | None = None,
    filename: str = '',
    request=None,
    *,
    file_bytes: bytes | None = None,
) -> ConversionOutcome:
    """
    Convert a file to markdown.

    Parameters
    ----------
    file_bytes_or_path:
        Either raw file bytes or a filesystem path to the file.
    file_bytes:
        Keyword alias for raw file bytes, used by `.eml` attachment extraction.
    filename:
        Original filename (used for extension routing and cache key).
    request:
        Optional FastAPI Request, used to build OWUI's Loader when falling
        back (passes through admin config like DOCLING_SERVER_URL, Tika, etc.).

    Returns
    -------
    ConversionOutcome
        Explicit complete/unavailable state. The public compatibility wrapper
        returns only ``markdown`` and therefore returns '' when unavailable.
    """
    file_bytes_or_path = _resolve_file_input(file_bytes_or_path, file_bytes, filename)

    # Resolve to bytes
    if isinstance(file_bytes_or_path, (str, os.PathLike)):
        try:
            with open(file_bytes_or_path, 'rb') as fh:
                raw_bytes = fh.read()
        except OSError as exc:
            log.error('skip-rag: file read failed; kind=%s', type(exc).__name__)
            return ConversionOutcome.unavailable('file_read_failed')
    else:
        raw_bytes = file_bytes_or_path

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    # --- Plain text: read directly, no conversion ---
    if ext in PLAINTEXT_EXTS:
        try:
            md = raw_bytes.decode('utf-8', errors='replace')
        except Exception:
            md = raw_bytes.decode('latin-1', errors='replace')
        return ConversionOutcome.complete(md) if md.strip() else ConversionOutcome.unavailable('empty_plaintext')

    # --- Cache lookup ---
    cache_key = _cache_key(raw_bytes, ext)
    cached = _cache_read(cache_key)
    if cached is not None:
        outcome = (
            ConversionOutcome.complete(cached) if cached.strip() else ConversionOutcome.unavailable('empty_cache_entry')
        )
        log.info(
            'skip-rag: conversion_outcome %s',
            json.dumps(
                {
                    'hash_prefix': cache_key[:LOG_HASH_PREFIX_LENGTH],
                    'extension': ext,
                    'outcome': outcome.status.value,
                    'failure_kind': outcome.failure_kind,
                    'cache_decision': 'hit_complete' if outcome.is_complete else 'hit_empty',
                },
                sort_keys=True,
            ),
        )
        return outcome

    # --- Conversion ---
    used_degraded_fallback = False
    if ext == 'pdf':
        try:
            outcome = _docling_pdf_outcome(raw_bytes, filename)
        except Exception:
            # Keep every PDF failure on the typed fail-closed path.  Exception
            # details are deliberately omitted because they may carry request
            # or document context; the canonical kind is enough to diagnose.
            log.warning(
                'skip-rag: docling_pdf_unexpected %s',
                json.dumps(
                    {
                        'outcome': 'unavailable',
                        'failure_kind': 'unexpected_error',
                    },
                    sort_keys=True,
                ),
            )
            outcome = ConversionOutcome.unavailable('unexpected_error')
        md = outcome.markdown
    else:
        try:
            md = _convert_uncached(raw_bytes, filename, ext, request=request)
            outcome = (
                ConversionOutcome.complete(md)
                if isinstance(md, str) and md.strip()
                else ConversionOutcome.unavailable('empty_conversion')
            )
        except Exception as exc:
            log.warning(
                'skip-rag: conversion failed for extension %s; fallback_kind=%s',
                ext,
                type(exc).__name__,
            )
            md = _fallback_loader(raw_bytes, filename, request)
            used_degraded_fallback = True
            outcome = (
                ConversionOutcome.complete(md)
                if isinstance(md, str) and md.strip()
                else ConversionOutcome.unavailable('fallback_empty')
            )

    # Cache only the normal docling/soffice conversion. Do not cache degraded
    # fallback output under the content hash: a transient sidecar outage should
    # not permanently poison future skip-rag turns for the same file.
    if outcome.is_complete and not used_degraded_fallback:
        cache_decision = 'write_complete' if _cache_write(cache_key, md) else 'write_failed'
    else:
        cache_decision = 'skip_unavailable' if outcome.is_unavailable else 'skip_degraded'

    log.info(
        'skip-rag: conversion_outcome %s',
        json.dumps(
            {
                'hash_prefix': cache_key[:LOG_HASH_PREFIX_LENGTH],
                'extension': ext,
                'outcome': outcome.status.value,
                'failure_kind': outcome.failure_kind,
                'cache_decision': cache_decision,
            },
            sort_keys=True,
        ),
    )
    return outcome


def _convert_uncached(raw_bytes: bytes, filename: str, ext: str, request=None) -> str:
    """
    Internal: run the actual conversion pipeline without touching the cache.
    Dedicated `.eml` parsing has priority over Docling and fallback loaders.
    Raises on failure (caller handles).
    """
    if ext in MSG_EXTS:
        from open_webui.skiprag.msg import extract_msg_text_from_bytes

        log.info('skip-rag: converting .msg via stdlib CFBF reader')
        return extract_msg_text_from_bytes(raw_bytes)

    if ext in EMAIL_EXTS:
        from open_webui.skiprag.email import eml_to_markdown

        return eml_to_markdown(
            raw_bytes,
            filename=filename,
            request=request,
            attachment_converter=convert_to_markdown,
        )

    elif ext in LEGACY_EXT_TO_TARGET:
        # Legacy → soffice → modern OOXML → docling
        target_ext = LEGACY_EXT_TO_TARGET[ext]
        log.info('skip-rag: converting legacy .%s → .%s via soffice then docling', ext, target_ext)
        modern_bytes = _soffice_convert(raw_bytes, filename, target_ext)
        modern_filename = filename[: -len(ext)] + target_ext
        return _docling_convert(modern_bytes, modern_filename, target_ext)

    elif ext in DOCLING_DIRECT_EXTS:
        log.info('skip-rag: converting .%s directly via docling', ext)
        return _docling_convert(raw_bytes, filename, ext)

    else:
        # Unknown extension — try docling; fallback triggered by caller on exception
        log.info('skip-rag: unknown ext .%s, attempting docling', ext)
        return _docling_convert(raw_bytes, filename, ext)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

_enc_cache: dict[str, tiktoken.Encoding] = {}


def _get_encoding() -> tiktoken.Encoding:
    name = TIKTOKEN_ENCODING
    if name not in _enc_cache:
        _enc_cache[name] = tiktoken.get_encoding(name)
    return _enc_cache[name]


def count_tokens(text: str) -> int:
    """Count tokens in text using the configured tiktoken encoding."""
    try:
        return len(_get_encoding().encode(text))
    except Exception:
        # Rough fallback: ~4 chars per token
        return len(text) // 4


# ---------------------------------------------------------------------------
# Phase 4 stub — oversize extraction
# ---------------------------------------------------------------------------

_TRUNCATION_MARKER = '\n\n[...truncated, pending query-aware extraction...]\n'


def extract_to_budget(items: list[str], query: str, target_tokens: int) -> list[str]:
    """
    STUB — Phase 4.

    For Phase 3, when the total markdown across all items exceeds
    SKIP_RAG_TRIGGER_TOKENS, perform a safe naive proportional truncation
    so the combined result is ≤ target_tokens.

    Phase 4 must replace this body with query-aware extraction
    (extractor model call per item, budget split proportional to size with
    a floor, summing ≤ TARGET_TOKENS).  The function signature must remain
    identical so Phase 4 can drop in a new body without touching callers.

    TODO Phase 4:
        - Call SKIP_RAG_EXTRACTOR_MODEL_ID to extract relevant passages
          from each item conditioned on `query`.
        - Split the target_tokens budget proportional to each item's token
          count, with a minimum per-item floor (e.g. 1000 tokens).
        - The compressed items are what get persisted into the user message.
    """
    total = sum(count_tokens(item) for item in items)
    if total <= TRIGGER_TOKENS:
        return items  # Nothing to do

    log.info(
        'skip-rag [Phase3-stub]: total %d tokens > trigger %d; naive proportional truncation to %d',
        total, TRIGGER_TOKENS, target_tokens,
    )

    enc = _get_encoding()
    # S2 fix: the truncation marker itself costs tokens; subtract its cost from
    # each per-item budget so the combined output (text + marker) never exceeds
    # target_tokens in total.
    marker_tokens = len(enc.encode(_TRUNCATION_MARKER))

    result: list[str] = []
    for item in items:
        tokens = enc.encode(item)
        item_tokens = len(tokens)
        if total > 0:
            # Each item gets a share proportional to its size
            raw_budget = max(256, int(target_tokens * item_tokens / total))
        else:
            raw_budget = target_tokens

        # Reserve space for the truncation marker so text+marker ≤ raw_budget
        effective_budget = max(1, raw_budget - marker_tokens)

        if item_tokens <= raw_budget:
            # Item fits within its share (no truncation needed)
            result.append(item)
        else:
            truncated_tokens = tokens[:effective_budget]
            truncated_text = enc.decode(truncated_tokens)
            result.append(truncated_text + _TRUNCATION_MARKER)

    return result

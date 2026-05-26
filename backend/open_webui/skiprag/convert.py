"""
skip-rag Phase 3 — file → markdown conversion module.

Public API
----------
convert_to_markdown(file_bytes_or_path, filename, request=None) -> str
    Convert a file to markdown.  Route:
      - .md / .txt           → read text directly (no docling)
      - legacy .doc/.xls/.ppt → POST soffice → modern bytes → docling
      - modern .docx/.xlsx/.pptx + .pdf + images → docling directly
    Cache: keyed by sha256(original bytes) + ext; stored under
    SKIP_RAG_CACHE_DIR (default /app/backend/skiprag-cache).
    Fallback: on any soffice/docling failure, use OWUI's Loader chain and
    log the downgrade.  Never raises — always returns some text.

count_tokens(text) -> int
    Tiktoken token count using TIKTOKEN_ENCODING_NAME env var.

extract_to_budget(items, query, target_tokens) -> list[str]
    Phase 3 STUB (proportional truncation only).  This stub is kept for
    backwards-compat but the Phase 4 real implementation lives in extract.py
    (async, extractor-model-aware).  inject.py imports from extract.py.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Union

import requests
import tiktoken

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment / configuration
# ---------------------------------------------------------------------------

DOCLING_SERVER_URL: str = os.environ.get('DOCLING_SERVER_URL', 'http://docling:5001').rstrip('/')
SOFFICE_URL: str = os.environ.get('SKIP_RAG_SOFFICE_URL', 'http://soffice:2004').rstrip('/')
CACHE_DIR: str = os.environ.get('SKIP_RAG_CACHE_DIR', '/app/backend/skiprag-cache')
OCR_PAGE_TIMEOUT: int = int(os.environ.get('SKIP_RAG_OCR_PAGE_TIMEOUT', '240'))
TRIGGER_TOKENS: int = int(os.environ.get('SKIP_RAG_TRIGGER_TOKENS', '200000'))
TARGET_TOKENS: int = int(os.environ.get('SKIP_RAG_TARGET_TOKENS', '180000'))
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

# Extensions that are just plain text — read directly
PLAINTEXT_EXTS: frozenset[str] = frozenset({'md', 'txt'})


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(raw_bytes: bytes, ext: str) -> str:
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return f'{digest}.{ext}.md'


def _cache_read(key: str) -> str | None:
    path = Path(CACHE_DIR) / key
    if path.exists():
        try:
            return path.read_text(encoding='utf-8')
        except OSError:
            return None
    return None


def _cache_write(key: str, text: str) -> None:
    _ensure_cache_dir()
    path = Path(CACHE_DIR) / key
    try:
        path.write_text(text, encoding='utf-8')
    except OSError as exc:
        log.warning('skip-rag: cache write failed: %s', exc)


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
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Docling conversion
# ---------------------------------------------------------------------------

def _count_pdf_pages(file_bytes: bytes) -> int:
    """Return page count for a PDF given its bytes; 1 on any error."""
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        return max(1, len(reader.pages))
    except Exception:
        return 1


def _docling_convert(file_bytes: bytes, filename: str, ext: str) -> str:
    """
    POST file bytes to docling-serve and return the markdown text.
    Timeout scales by page count for PDFs (OCR_PAGE_TIMEOUT × pages).
    Raises requests.RequestException on failure.
    """
    if ext == 'pdf':
        pages = _count_pdf_pages(file_bytes)
        timeout = OCR_PAGE_TIMEOUT * pages
        log.info('skip-rag: PDF has %d page(s), docling timeout=%ds', pages, timeout)
    else:
        timeout = 120

    resp = requests.post(
        f'{DOCLING_SERVER_URL}/v1/convert/file',
        files={'files': (filename, file_bytes, 'application/octet-stream')},
        data={'to_formats': 'md', 'image_export_mode': 'placeholder'},
        timeout=timeout,
    )
    resp.raise_for_status()
    result = resp.json()
    # docling-serve response: {"document": {"md_content": "..."}} or list
    if isinstance(result, list):
        result = result[0] if result else {}
    doc = result.get('document', result)
    md = doc.get('md_content') or doc.get('content', '')
    return md


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
        log.error('skip-rag: fallback loader also failed for %s: %s', filename, exc)
        return ''
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Main conversion entry point
# ---------------------------------------------------------------------------

def convert_to_markdown(
    file_bytes_or_path: Union[bytes, str, os.PathLike],
    filename: str,
    request=None,
) -> str:
    """
    Convert a file to markdown.

    Parameters
    ----------
    file_bytes_or_path:
        Either raw file bytes or a filesystem path to the file.
    filename:
        Original filename (used for extension routing and cache key).
    request:
        Optional FastAPI Request, used to build OWUI's Loader when falling
        back (passes through admin config like DOCLING_SERVER_URL, Tika, etc.).

    Returns
    -------
    str
        Markdown text.  Never raises; on total failure returns ''.
    """
    # Resolve to bytes
    if isinstance(file_bytes_or_path, (str, os.PathLike)):
        try:
            with open(file_bytes_or_path, 'rb') as fh:
                raw_bytes = fh.read()
        except OSError as exc:
            log.error('skip-rag: cannot read file %s: %s', file_bytes_or_path, exc)
            return ''
    else:
        raw_bytes = file_bytes_or_path

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    # --- Plain text: read directly, no conversion ---
    if ext in PLAINTEXT_EXTS:
        try:
            return raw_bytes.decode('utf-8', errors='replace')
        except Exception:
            return raw_bytes.decode('latin-1', errors='replace')

    # --- Cache lookup ---
    cache_key = _cache_key(raw_bytes, ext)
    cached = _cache_read(cache_key)
    if cached is not None:
        log.info('skip-rag: cache hit for %s (%s)', filename, cache_key[:16])
        return cached

    # --- Conversion ---
    used_degraded_fallback = False
    try:
        md = _convert_uncached(raw_bytes, filename, ext)
    except Exception as exc:
        log.warning('skip-rag: conversion failed for %s (%s), falling back to OWUI Loader: %s', filename, ext, exc)
        md = _fallback_loader(raw_bytes, filename, request)
        used_degraded_fallback = True
        if md:
            log.info('skip-rag: fallback loader succeeded for %s, len=%d', filename, len(md))
        else:
            log.error('skip-rag: fallback loader returned empty for %s', filename)

    # Cache only the normal docling/soffice conversion. Do not cache degraded
    # fallback output under the content hash: a transient sidecar outage should
    # not permanently poison future skip-rag turns for the same file.
    if md and not used_degraded_fallback:
        _cache_write(cache_key, md)

    return md


def _convert_uncached(raw_bytes: bytes, filename: str, ext: str) -> str:
    """
    Internal: run the actual conversion pipeline without touching the cache.
    Raises on failure (caller handles).
    """
    if ext in LEGACY_EXT_TO_TARGET:
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

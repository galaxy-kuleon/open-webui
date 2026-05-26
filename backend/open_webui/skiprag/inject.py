"""
skip-rag Phases 3/4 — Path A injection (pure transient, per-request).

This module handles the Path A flow:
  1. Gate: model has capability `skip_rag` AND model id NOT in Hermes bridge IDs
  2. For each file in the upload turn: convert to md via convert_to_markdown (cached)
  3. REMOVE the skip_rag file items from body['metadata']['files'] so that
     chat_completion_files_handler / get_sources_from_items NEVER embeds or
     injects them through the native RAG pipeline.
  4. Build a <source>-wrapped context block and inject it TRANSIENTLY into the
     LAST USER message in body['messages'] — every request, never persisted to DB.

Transient injection model
--------------------------
The md is NOT stored in the chat DB.  The user's persisted chat bubble remains
their original typed text.  On every request the middleware calls
run_skip_rag_path_a which re-reads the cached md file(s) (cache hit, ~0 ms) and
injects them into the messages array before forwarding to the LLM.  This ensures:
  • No md leaks into the user's chat history or bubble
  • No sentinel / double-injection problem
  • Full md is available to the LLM on every turn where file items are present

File removal from metadata.files
----------------------------------
After converting, we remove the processed file items (type=='file') from
body['metadata']['files'].  This prevents OWUI's native pipeline from:
  • embedding them into the vector DB (save_docs_to_vector_db)
  • injecting them via get_sources_from_items (which uses context='full' items)
  • exposing them to later tools/code-interpreter paths that read __files__
    from metadata.files
We do NOT set context='full' — that was the source of double-injection.
Non-file items (collections, knowledge bases) are left untouched.

Phase 5 gate
------------
When model id ∈ OPENWEBUI_HERMES_BRIDGE_MODEL_IDS, this module returns False
from is_skip_rag_model (Path B / Hermes handoff — not yet implemented).

TODO Phase 5:
    Implement Path B here or in a separate hermes_handoff.py module.
    Write {original, md} to the hermes-handoff shared volume, inject a
    <files> block into the outgoing message, skip embedding entirely.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

from open_webui.skiprag.convert import (
    convert_to_markdown,
    count_tokens,
    TRIGGER_TOKENS,
    TARGET_TOKENS,
)
from open_webui.skiprag.extract import extract_to_budget

log = logging.getLogger(__name__)

# Hermes bridge model IDs — Phase 5 gate
_HERMES_MODEL_IDS: frozenset[str] = frozenset(
    m.strip()
    for m in os.environ.get('OPENWEBUI_HERMES_BRIDGE_MODEL_IDS', '').split(',')
    if m.strip()
)


def is_skip_rag_model(model: dict) -> bool:
    """
    Return True if this model should trigger Path A (skip_rag, non-Hermes).

    Rules (plan §2):
    1. model id MUST NOT be in OPENWEBUI_HERMES_BRIDGE_MODEL_IDS (that is Path B / Phase 5)
    2. model MUST have capability skip_rag == True
    """
    model_id = model.get('id', '')
    if model_id in _HERMES_MODEL_IDS:
        return False

    capabilities = (model.get('info', {}).get('meta', {}).get('capabilities') or {})
    return bool(capabilities.get('skip_rag', False))


def _build_md_block(filename: str, md: str) -> str:
    """
    Wrap a single file's markdown in a <source> tag that mirrors OWUI's
    get_source_context() style (middleware.py:948-971).
    """
    return (
        f'<source name="{html.escape(filename, quote=True)}" resource-type="file">'
        f'\n{md.strip()}\n'
        f'</source>\n'
    )


def _build_injection_block(file_mds: list[tuple[str, str]]) -> str:
    """
    Build the full injection block for all files (after Phase 4 extraction if
    oversize).  Returns a string ready to append to the user message content.
    """
    sources = '\n'.join(_build_md_block(name, md) for name, md in file_mds)
    return f'\n\n{sources}'


def _inject_transiently(body: dict, injection_block: str) -> None:
    """
    Inject the md block transiently into the LAST USER message in the
    in-memory messages array.  Never touches the DB.

    Handles both string content and multimodal (list of content parts) content.
    """
    messages = body.get('messages', [])
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            content = msg.get('content', '')
            if isinstance(content, list):
                # Multimodal: find the first text part and append there
                text_parts = [p for p in content if isinstance(p, dict) and p.get('type') == 'text']
                if text_parts:
                    text_parts[0]['text'] = text_parts[0]['text'] + injection_block
                else:
                    # No text part yet — prepend one
                    msg['content'] = [{'type': 'text', 'text': injection_block}] + list(content)
            else:
                msg['content'] = (content or '') + injection_block
            return


async def run_skip_rag_path_a(
    request,
    body: dict,
    extra_params: dict,
    user,
) -> tuple[dict, dict]:
    """
    Path A handler.  Called from process_chat_payload when is_skip_rag_model.

    Actions:
      1. Convert each skip_rag file item to markdown (cached).
      2. Remove the skip_rag file items from body['metadata']['files'] so
         OWUI's native embedding/retrieval pipeline never sees them.
      3. Inject the <source>-wrapped md transiently into the last user message
         in body['messages'].  No DB writes; no persistence.

    Returns
    -------
    (body, flags)
        body: the modified form_data body (skip_rag file items removed from
              metadata.files; last user message has md appended)
        flags: {'sources': []} — we skip vector-db, so no RAG sources
    """
    metadata = body.get('metadata', {})
    files = metadata.get('files', [])

    if not files:
        return body, {'sources': []}

    # Partition: file items the skip-rag pipeline will handle vs. everything else
    # (collections, knowledge bases, etc. are left untouched in the list).
    skip_rag_files = [f for f in files if f.get('type', 'file') == 'file']
    other_files = [f for f in files if f.get('type', 'file') != 'file']

    if not skip_rag_files:
        return body, {'sources': []}

    # --- Convert each file to markdown ---
    file_mds: list[tuple[str, str]] = []  # (filename, md)
    for file_item in skip_rag_files:
        file_id = file_item.get('id') or file_item.get('file_id')
        filename = file_item.get('name', 'file')

        md = await _load_and_convert(file_item, filename, request)

        if md:
            file_mds.append((filename, md))
        else:
            log.warning('skip-rag: got empty md for file %s (%s)', filename, file_id)

    # --- Remove processed skip_rag file items from metadata.files ---
    # This prevents OWUI's native pipeline (chat_completion_files_handler /
    # get_sources_from_items) from embedding or injecting them.
    # Non-file items (collections, knowledge) are preserved.
    metadata['files'] = other_files
    if 'metadata' in body:
        body['metadata']['files'] = other_files

    if not file_mds:
        log.warning('skip-rag: no markdown produced for any file; skip injection')
        return body, {'sources': []}

    # --- Phase 4: oversize check + query-aware extraction ---
    mds = [md for _, md in file_mds]
    total_tokens = sum(count_tokens(md) for md in mds)

    if total_tokens > TRIGGER_TOKENS:
        log.info(
            'skip-rag: total %d tokens > trigger %d; running Phase 4 query-aware extraction',
            total_tokens, TRIGGER_TOKENS,
        )
        from open_webui.utils.misc import get_last_user_message
        query = get_last_user_message(body.get('messages', [])) or ''
        # Phase 4: async extract_to_budget with extractor model call
        extracted = await extract_to_budget(mds, query, TARGET_TOKENS, request=request)
        file_mds = [(name, t) for (name, _), t in zip(file_mds, extracted)]

    # --- Build and inject the md block transiently into the last user message ---
    injection_block = _build_injection_block(file_mds)
    _inject_transiently(body, injection_block)

    log.info(
        'skip-rag: Path A done (transient, Phase 4) — %d file(s), %d md chars injected, '
        'skip_rag files removed from metadata.files',
        len(file_mds),
        sum(len(md) for _, md in file_mds),
    )

    return body, {'sources': []}


async def _load_and_convert(file_item: dict, filename: str, request) -> str:
    """
    Resolve the physical file bytes from the file_item and call convert_to_markdown.
    Tries file.path (via OWUI Storage), falls back to the file DB record.
    """
    file_path = file_item.get('path') or file_item.get('file', {}).get('path')
    if file_path:
        try:
            from open_webui.storage.provider import Storage
            local_path = Storage.get_file(file_path)
            return await asyncio.to_thread(convert_to_markdown, local_path, filename, request)
        except Exception as exc:
            log.warning('skip-rag: Storage.get_file failed for %s: %s', filename, exc)

    # Fallback: look up the file DB record (async — we are already in an async context)
    file_id = file_item.get('id') or file_item.get('file_id')
    if file_id:
        try:
            from open_webui.models.files import Files

            file_model = await Files.get_file_by_id(file_id)

            if file_model:
                # Try to get local path via Storage
                if file_model.path:
                    from open_webui.storage.provider import Storage
                    local_path = Storage.get_file(file_model.path)
                    return await asyncio.to_thread(convert_to_markdown, local_path, filename, request)

                # Last resort: use text content stored in file.data
                content = (file_model.data or {}).get('content', '')
                if content:
                    return content
        except Exception as exc:
            log.warning('skip-rag: file DB lookup failed for %s: %s', filename, exc)

    log.error('skip-rag: cannot resolve bytes for file %s', filename)
    return ''

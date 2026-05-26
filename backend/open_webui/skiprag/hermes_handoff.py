"""
skip-rag Phase 5 — Path B: Hermes handoff.

When the selected model is a Hermes bridge model (id ∈
OPENWEBUI_HERMES_BRIDGE_MODEL_IDS) and files are attached, this module:

  1. Resolves each uploaded file's bytes (via Storage / DB, same logic as Path A).
  2. Converts each file to markdown via convert_to_markdown (cached).
  3. Writes BOTH the original file bytes AND the .md into the shared
     hermes-handoff volume under a per-user/per-chat/per-message subdirectory:
       /handoff/user/<user_id>/chat/<chat_id_or_nochat>/message/<message_id_or_uuid>/<unique_filename>
       /handoff/user/<user_id>/chat/<chat_id_or_nochat>/message/<message_id_or_uuid>/<unique_filename>.md
  4. REMOVES the file items from body['metadata']['files'] so that OWUI's
     native RAG pipeline (chat_completion_files_handler / save_docs_to_vector_db
     / get_sources_from_items) NEVER embeds or injects them.  Consequence:
     later OWUI tools/code-interpreter paths that read __files__ from
     metadata.files will not see these file items; Hermes receives the
     explicit /handoff paths instead.
  5. Injects a structured <files> block into the LAST USER message of
     body['messages'] listing the paths AS HERMES SEES THEM — identical
     /handoff/... path because the volume is mounted at the same path in
     both containers.

     Example injected block:
         <files>
           <file name="report.pdf"
                 user="<openwebui-user-id>"
                 chat="<chat-id>"
                 original="/handoff/user/.../report.pdf"
                 markdown="/handoff/user/.../report.pdf.md"
                 sig="<hmac>"/>
         </files>

Design assumptions
------------------
- Path B fires for ANY Hermes bridge model (id ∈
  OPENWEBUI_HERMES_BRIDGE_MODEL_IDS) that has files, regardless of the
  model's `skip_rag` capability flag.  The rationale: when the user selects
  a Hermes agent and attaches files, we ALWAYS want the handoff path — the
  `skip_rag` capability toggle is a Path A concern (non-Hermes models with
  full-context injection).  This is documented here and in the gate comment
  in utils/middleware.py.
- Retention: handoff artifacts are NOT cleaned up — the hermes-handoff
  volume accumulates.  Revisit in a later maintenance cycle.

Hermes-side consumption
-----------------------
The Hermes agent (hermes-agent submodule) parses the <files> block, verifies
the user/chat scope and HMAC signature, reads each listed /handoff markdown
path from the shared volume, and appends bounded file context to the agent
input.  OWUI remains responsible for conversion + signed handoff creation.

Environment variables
---------------------
SKIP_RAG_HANDOFF_DIR   Root of the handoff volume inside OWUI (default: /handoff).
SKIP_RAG_HANDOFF_SIGNING_KEY  Shared HMAC key used to prove OWUI generated each
                              handoff entry (default: HERMES_BRIDGE_API_KEY).
OPENWEBUI_HERMES_BRIDGE_MODEL_IDS  Comma-separated model IDs treated as Hermes
                                   bridge models (reused from inject.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import uuid
import html
from pathlib import Path
from typing import Optional

from open_webui.skiprag.convert import convert_to_markdown

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HANDOFF_DIR: str = os.environ.get('SKIP_RAG_HANDOFF_DIR', '/handoff')
HANDOFF_SIGNING_KEY: str = os.environ.get(
    'SKIP_RAG_HANDOFF_SIGNING_KEY',
    os.environ.get('HERMES_BRIDGE_API_KEY', ''),
)
_SAFE_SEGMENT_RE = re.compile(r'[^a-zA-Z0-9_.-]+')

# Mirror of _HERMES_MODEL_IDS in inject.py — duplicated intentionally so this
# module has no import dependency on inject.py (avoids circular imports).
_HERMES_MODEL_IDS: frozenset[str] = frozenset(
    m.strip()
    for m in os.environ.get('OPENWEBUI_HERMES_BRIDGE_MODEL_IDS', '').split(',')
    if m.strip()
)


# ---------------------------------------------------------------------------
# Public gate
# ---------------------------------------------------------------------------

def is_hermes_handoff(model: dict) -> bool:
    """
    Return True if this model should trigger Path B (Hermes handoff).

    Rule: model id ∈ OPENWEBUI_HERMES_BRIDGE_MODEL_IDS.
    Note: NOT gated by the skip_rag capability — any Hermes model triggers
    Path B regardless of that flag (see design assumptions above).
    """
    model_id = model.get('id', '')
    return model_id in _HERMES_MODEL_IDS


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_segment(raw: Optional[str], default: str) -> str:
    """Return a filesystem-safe, traversal-proof path segment."""
    cleaned = _SAFE_SEGMENT_RE.sub('_', (raw or '').strip())[:96].strip('._-')
    if not cleaned or '..' in cleaned:
        return default
    return cleaned


def _user_id_from_user(user) -> str:
    """Extract a stable OpenWebUI user id for handoff namespacing."""
    return _safe_segment(getattr(user, 'id', None), 'nouser')


def _sign_handoff_entry(user_id: str, chat_id: str, original_path: str, md_path: str) -> str:
    """
    Sign one handoff entry with the user/chat scope and absolute paths.

    Hermes verifies this against the forwarded OpenWebUI user/chat headers, so
    an arbitrary prompt-level <files> block cannot read another user's files.
    """
    if not HANDOFF_SIGNING_KEY:
        return ''
    payload = '\0'.join([user_id, chat_id, original_path, md_path]).encode('utf-8')
    return hmac.new(HANDOFF_SIGNING_KEY.encode('utf-8'), payload, hashlib.sha256).hexdigest()


def _make_handoff_subdir(user_id: str, chat_id: Optional[str], message_id: Optional[str]) -> Path:
    """
    Build and create the per-user/per-chat/per-message subdirectory under HANDOFF_DIR.

    Layout:
        /handoff/user/<user_id>/chat/<chat_id or 'nochat'>/message/<message_id or <uuid4>>/
    """
    safe_user = _safe_segment(user_id, 'nouser')
    safe_chat = _safe_segment(chat_id, 'nochat')
    safe_msg = _safe_segment(message_id, str(uuid.uuid4()))
    subdir = Path(HANDOFF_DIR) / 'user' / safe_user / 'chat' / safe_chat / 'message' / safe_msg
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir


def _write_handoff_files(
    subdir: Path,
    filename: str,
    raw_bytes: bytes,
    md: str,
    index: int,
) -> tuple[str, str]:
    """
    Write the original file bytes and the markdown to the handoff subdir.

    Returns (original_path, md_path) as absolute strings that are valid
    inside BOTH the owui and hermes containers (identical mount path).
    """
    # Sanitise filename: no path separators. Prefix with index+nonce so two
    # same-named uploads in one message cannot overwrite each other.
    safe_name = _safe_segment(Path(filename).name or 'file', 'file')
    unique_name = f'{index:03d}-{uuid.uuid4().hex[:8]}-{safe_name}'

    orig_path = subdir / unique_name
    md_path = subdir / (unique_name + '.md')

    orig_path.write_bytes(raw_bytes)
    md_path.write_text(md, encoding='utf-8')

    return str(orig_path), str(md_path)


def _build_files_block(entries: list[tuple[str, str, str, str, str]]) -> str:
    """
    Build the <files>…</files> XML block to inject into the user message.

    entries: list of (display_name, original_path, md_path, user_id, chat_id)
    """
    lines = ['<files>']
    for name, orig, md_p, user_id, chat_id in entries:
        sig = _sign_handoff_entry(user_id, chat_id, orig, md_p)
        lines.append(
            f'  <file name="{html.escape(name, quote=True)}"'
            f' user="{html.escape(user_id, quote=True)}"'
            f' chat="{html.escape(chat_id, quote=True)}"'
            f' original="{html.escape(orig, quote=True)}"'
            f' markdown="{html.escape(md_p, quote=True)}"'
            f' sig="{html.escape(sig, quote=True)}"/>'
        )
    lines.append('</files>')
    return '\n'.join(lines)


def _inject_transiently(body: dict, block: str) -> None:
    """
    Append the <files> block transiently to the LAST USER message.

    Handles both plain-string content and multimodal (list of parts) content.
    Mirrors the same helper in inject.py.
    """
    messages = body.get('messages', [])
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            content = msg.get('content', '')
            injection = f'\n\n{block}'
            if isinstance(content, list):
                text_parts = [
                    p for p in content
                    if isinstance(p, dict) and p.get('type') == 'text'
                ]
                if text_parts:
                    text_parts[0]['text'] = text_parts[0]['text'] + injection
                else:
                    msg['content'] = [{'type': 'text', 'text': injection}] + list(content)
            else:
                msg['content'] = (content or '') + injection
            return


async def _resolve_raw_bytes(file_item: dict, filename: str) -> bytes:
    """
    Resolve the raw bytes for a file item.

    Resolution order (same as inject.py _load_and_convert, but returns bytes
    not md — Path B needs the original bytes to write to the handoff volume):
      1. file_item['path'] / file_item['file']['path'] → Storage.get_file → read
      2. file DB record → Storage.get_file → read
      3. file DB record → data['content'] encoded as utf-8 (text fallback)
    Returns b'' on total failure.
    """
    file_path = file_item.get('path') or file_item.get('file', {}).get('path')
    if file_path:
        try:
            from open_webui.storage.provider import Storage
            local_path = Storage.get_file(file_path)
            return Path(local_path).read_bytes()
        except Exception as exc:
            log.warning('hermes-handoff: Storage.get_file failed for %s: %s', filename, exc)

    file_id = file_item.get('id') or file_item.get('file_id')
    if file_id:
        try:
            from open_webui.models.files import Files
            file_model = await Files.get_file_by_id(file_id)
            if file_model:
                if file_model.path:
                    from open_webui.storage.provider import Storage
                    local_path = Storage.get_file(file_model.path)
                    return Path(local_path).read_bytes()
                content = (file_model.data or {}).get('content', '')
                if content:
                    return content.encode('utf-8')
        except Exception as exc:
            log.warning('hermes-handoff: file DB lookup failed for %s: %s', filename, exc)

    log.error('hermes-handoff: cannot resolve bytes for file %s', filename)
    return b''


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------

async def run_hermes_handoff(
    request,
    body: dict,
    extra_params: dict,
    user,
) -> dict:
    """
    Path B handler.  Called from process_chat_payload when is_hermes_handoff.

    Actions:
      1. Identify file items in body['metadata']['files'].
      2. Resolve raw bytes + convert to md for each file.
      3. Write original + .md into the shared handoff volume.
      4. Remove the processed file items from body['metadata']['files']
         (prevents OWUI native embedding / RAG injection).
      5. Inject a <files> block into the last user message.

    Returns
    -------
    body: the modified form_data body (file items removed from
          metadata.files; last user message has <files> block appended).
    """
    metadata = body.get('metadata', {})
    files = metadata.get('files', [])

    if not files:
        return body

    # Partition: file items vs. everything else (collections, knowledge bases)
    file_items = [f for f in files if f.get('type', 'file') == 'file']
    other_items = [f for f in files if f.get('type', 'file') != 'file']

    if not file_items:
        return body

    # Determine the handoff subdir from user/chat/message context. User/chat
    # scope is also signed into each <file> entry and re-checked by Hermes.
    user_id = _user_id_from_user(user)
    chat_id: str = _safe_segment(metadata.get('chat_id') or body.get('chat_id'), 'nochat')
    # Try to derive a message_id from the last user message (if present)
    message_id: Optional[str] = None
    for msg in reversed(body.get('messages', [])):
        if msg.get('role') == 'user':
            message_id = msg.get('id')
            break

    subdir = _make_handoff_subdir(user_id, chat_id, message_id)

    entries: list[tuple[str, str, str, str, str]] = []  # (name, orig_path, md_path, user_id, chat_id)

    for idx, file_item in enumerate(file_items, start=1):
        filename = file_item.get('name', 'file')

        # 1. Resolve raw bytes
        raw_bytes = await _resolve_raw_bytes(file_item, filename)
        if not raw_bytes:
            log.warning('hermes-handoff: skipping %s — could not resolve bytes', filename)
            continue

        # 2. Convert to markdown (cached via convert_to_markdown)
        md = await asyncio.to_thread(convert_to_markdown, raw_bytes, filename, request)
        if not md:
            log.warning('hermes-handoff: empty md for %s — using empty string', filename)
            md = ''

        # 3. Write to shared handoff volume
        try:
            orig_path, md_path = _write_handoff_files(subdir, filename, raw_bytes, md, idx)
        except OSError as exc:
            log.error(
                'hermes-handoff: failed to write %s to handoff volume %s: %s',
                filename, subdir, exc,
            )
            continue

        entries.append((filename, orig_path, md_path, user_id, chat_id))
        log.info(
            'hermes-handoff: wrote %s → original=%s md=%s (md_len=%d)',
            filename, orig_path, md_path, len(md),
        )

    # 4. Remove processed file items from metadata.files (no embedding, no RAG)
    metadata['files'] = other_items
    if 'metadata' in body:
        body['metadata']['files'] = other_items

    if not entries:
        log.warning('hermes-handoff: no files successfully written to handoff volume')
        return body

    # 5. Inject <files> block into the last user message (transient, not persisted)
    files_block = _build_files_block(entries)
    _inject_transiently(body, files_block)

    log.info(
        'hermes-handoff: Path B done — %d file(s) handed off under %s, '
        'file items removed from metadata.files, <files> block injected',
        len(entries), subdir,
    )

    return body

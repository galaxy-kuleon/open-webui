"""
skip-rag Phase 5 — Path B: Hermes handoff.

When the selected model is a Hermes bridge model (id ∈
OPENWEBUI_HERMES_BRIDGE_MODEL_IDS) and files are attached, this module:

  1. Resolves each uploaded file's bytes (via Storage / DB, same logic as Path A).
  2. Writes ONLY the original file bytes into the shared hermes-handoff volume
     under a per-user/per-chat/per-message subdirectory:
       /handoff/user/<user_id>/chat/<chat_id_or_nochat>/message/<message_id_or_uuid>/<unique_filename>

     NOTE: No Docling/markdown conversion is performed. The Hermes agent has
     its own tools (read_file, terminal, vision_analyze) to process files
     as needed. This avoids unnecessary conversion delays and timeouts.

  3. REMOVES the file items from body['metadata']['files'] so that OWUI's
     native RAG pipeline (chat_completion_files_handler / save_docs_to_vector_db
     / get_sources_from_items) NEVER embeds or injects them.  Consequence:
     later OWUI tools/code-interpreter paths that read __files__ from
     metadata.files will not see these file items; Hermes receives the
     explicit /handoff paths instead.
  4. Injects a structured <files> block into the LAST USER message of
     body['messages'] listing the paths AS HERMES SEES THEM — identical
     /handoff/... path because the volume is mounted at the same path in
     both containers.

     Example injected block:
         <files>
           <file original="/handoff/user/.../report.pdf"
                 file_id="<openwebui-file-id>"
                 sha256="<content-sha256>"
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
- NO markdown conversion: Hermes agent decides independently how to process
  files using its own toolset. This is the core philosophy of skip-rag mode
  for Hermes — avoid pre-hydrating content into messages.
- Retention: handoff artifacts are NOT cleaned up — the hermes-handoff
  volume accumulates.  Revisit in a later maintenance cycle.

Hermes-side consumption
-----------------------
The Hermes agent (hermes-agent submodule) parses the <files> block, verifies
the user/chat scope and HMAC signature, and receives only the file paths.
The agent then decides independently whether and how to process each file
using its own tools (read_file, terminal commands like pdftotext/catdoc,
vision_analyze for images, etc.).

Environment variables
---------------------
SKIP_RAG_HANDOFF_DIR   Root of the handoff volume inside OWUI (default: /handoff).
SKIP_RAG_HANDOFF_SIGNING_KEY  Shared HMAC key used to prove OWUI generated each
                              handoff entry (default: HERMES_BRIDGE_API_KEY).
OPENWEBUI_HERMES_BRIDGE_MODEL_IDS  Comma-separated model IDs treated as Hermes
                                   bridge models (reused from inject.py).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from filelock import FileLock, Timeout as FileLockTimeout
from starlette.concurrency import run_in_threadpool

from open_webui.utils.handoff_filename import build_attachment_basename

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
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
HANDOFF_MANIFEST_NAME = '.owui-handoff-manifest.json'
HANDOFF_MANIFEST_LOCK_NAME = '.owui-handoff-manifest.lock'
HANDOFF_MANIFEST_MAX_BYTES = 128 * 1024
HANDOFF_MANIFEST_LOCK_TIMEOUT_SECONDS = 10

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


def _sign_handoff_entry(
    user_id: str,
    chat_id: str,
    original_path: str,
    file_id: str = '',
    sha256: str = '',
) -> str:
    """
    Sign one handoff entry with the user/chat scope, path, and stable identity.

    Hermes verifies this against the forwarded OpenWebUI user/chat headers, so
    an arbitrary prompt-level <files> block cannot read another user's files.
    """
    if not HANDOFF_SIGNING_KEY:
        return ''
    parts = [user_id, chat_id, original_path]
    if file_id or sha256:
        parts.extend([file_id, sha256])
    payload = '\0'.join(parts).encode('utf-8')
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
    index: int,
) -> str:
    """
    Write the original file bytes to the handoff subdir.

    Returns original_path as an absolute string that is valid inside BOTH the
    owui and hermes containers (identical mount path).
    """
    # Prefix with index+random nonce so same-named uploads cannot overwrite.
    # Attachment basenames have a separate suffix-preserving policy; identity
    # path segments remain owned by _safe_segment.
    unique_name = build_attachment_basename(
        filename,
        index=index,
        nonce=uuid.uuid4().hex[:8],
    )

    orig_path = subdir / unique_name

    orig_path.write_bytes(raw_bytes)

    return str(orig_path)


def _file_id_from_item(file_item: dict) -> str:
    """Return the most stable OpenWebUI file id exposed by a file item."""
    file_id = file_item.get('id') or file_item.get('file_id')
    if not file_id and isinstance(file_item.get('file'), dict):
        file_id = file_item['file'].get('id')
    return str(file_id or '')


def _normalised_file_items(value: object, *, source: str) -> list:
    """Coerce a file-items field to a list, distinguishing absent from invalid.

    ``None`` means "no files" and normalises silently — it is what an omitted
    request field becomes. Anything else that is not a list is a schema
    violation: it is still treated as no files so one malformed field cannot
    strand a user's whole upload, but it is logged so a bad payload does not
    masquerade as an ordinary fileless turn. Truthiness is deliberately not
    used: `False` and `0` are violations, not empty collections.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    log.warning(
        'hermes-handoff: %s is %s, not a list — treating as no files',
        source,
        type(value).__name__,
    )
    return []


def _current_turn_file_items(metadata: dict, all_files: list[dict]) -> list[dict]:
    """
    Return file items attached to the current user message only.

    OpenWebUI request metadata.files is a chat-level superset: it contains
    historical chat files plus the current upload. For Hermes Path B, the
    persisted user_message.files field is the authoritative current-turn set.
    Legacy/direct callers without user_message metadata fall back to all files.

    ``all_files`` is normalised here as well as at the call site because this
    helper is reachable directly and its contract — never raise on a missing
    file list — should not depend on which caller reached it.
    """
    all_files = _normalised_file_items(all_files, source='all_files')
    user_message = metadata.get('user_message')
    if isinstance(user_message, dict):
        if 'files' in user_message:
            current_files = _normalised_file_items(
                user_message.get('files'), source='user_message.files'
            )
            return [
                f for f in current_files
                if isinstance(f, dict) and f.get('type', 'file') == 'file'
            ]
        if user_message.get('id'):
            return []

    return [
        f for f in all_files
        if isinstance(f, dict) and f.get('type', 'file') == 'file'
    ]


def _dedupe_file_items(file_items: list[dict]) -> list[dict]:
    """Dedupe exact current-turn file item repeats before byte resolution."""
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for file_item in file_items:
        file_id = _file_id_from_item(file_item)
        if file_id:
            key = ('file_id', file_id)
        else:
            key = (
                'fallback',
                '|'.join(
                    [
                        str(file_item.get('name') or ''),
                        str(file_item.get('path') or file_item.get('url') or ''),
                    ]
                ),
            )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(file_item)
    return deduped


def _build_files_block(entries: list[dict[str, str]]) -> str:
    """
    Build the <files>…</files> XML block to inject into the user message.
    """
    lines = ['<files>']
    for entry in entries:
        orig = entry['original']
        file_id = entry.get('file_id', '')
        sha256 = entry.get('sha256', '')
        sig = _sign_handoff_entry(
            entry['user_id'],
            entry['chat_id'],
            orig,
            file_id=file_id,
            sha256=sha256,
        )
        attrs = [
            f'original="{html.escape(orig, quote=True)}"',
        ]
        if file_id:
            attrs.append(f'file_id="{html.escape(file_id, quote=True)}"')
        if sha256:
            attrs.append(f'sha256="{html.escape(sha256, quote=True)}"')
        attrs.append(f'sig="{html.escape(sig, quote=True)}"')
        lines.append(
            f'  <file {" ".join(attrs)}/>'
        )
    lines.append('</files>')
    return '\n'.join(lines)


def _chat_handoff_dir(user_id: str, chat_id: str) -> Path:
    return Path(HANDOFF_DIR) / 'user' / user_id / 'chat' / chat_id


def _manifest_enabled(metadata: dict, user_id: str, chat_id: str) -> bool:
    user_message = metadata.get('user_message')
    return (
        user_id != 'nouser'
        and chat_id != 'nochat'
        and isinstance(user_message, dict)
        and bool(user_message.get('id'))
    )


def _valid_manifest_entry(
    raw: object,
    *,
    user_id: str,
    chat_id: str,
    allowed_root: Path,
) -> Optional[dict[str, str]]:
    if not isinstance(raw, dict) or not HANDOFF_SIGNING_KEY:
        return None
    original = str(raw.get('original') or '')
    file_id = str(raw.get('file_id') or '')
    sha256 = str(raw.get('sha256') or '')
    supplied_sig = str(raw.get('sig') or '')
    if not original or not _SHA256_RE.fullmatch(sha256) or not supplied_sig:
        return None
    try:
        candidate = Path(original).resolve()
        candidate.relative_to(allowed_root)
    except (OSError, RuntimeError, ValueError):
        return None
    expected_sig = _sign_handoff_entry(
        user_id,
        chat_id,
        str(candidate),
        file_id=file_id,
        sha256=sha256,
    )
    if not hmac.compare_digest(expected_sig, supplied_sig):
        return None
    return {
        'original': str(candidate),
        'user_id': user_id,
        'chat_id': chat_id,
        'file_id': file_id,
        'sha256': sha256,
        'sig': supplied_sig,
    }


def _read_manifest_entries(user_id: str, chat_id: str) -> list[dict[str, str]]:
    chat_root = _chat_handoff_dir(user_id, chat_id)
    manifest_path = chat_root / HANDOFF_MANIFEST_NAME
    try:
        if manifest_path.stat().st_size > HANDOFF_MANIFEST_MAX_BYTES:
            log.warning('hermes-handoff: rejecting oversized manifest %s', manifest_path)
            return []
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        if payload.get('version') != 1 or payload.get('scope') != {
            'user_id': user_id,
            'chat_id': chat_id,
        }:
            return []
        raw_entries = payload.get('entries')
        if not isinstance(raw_entries, list):
            return []
        allowed_root = chat_root.resolve()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return []

    entries: list[dict[str, str]] = []
    for raw in raw_entries:
        entry = _valid_manifest_entry(
            raw,
            user_id=user_id,
            chat_id=chat_id,
            allowed_root=allowed_root,
        )
        if entry is not None:
            entries.append(entry)
    return entries


class HandoffManifestCapacityError(ValueError):
    """Raised when chat continuity cannot fit in the bounded manifest."""


def _atomic_write_manifest(
    user_id: str,
    chat_id: str,
    entries: list[dict[str, str]],
) -> None:
    chat_root = _chat_handoff_dir(user_id, chat_id)
    chat_root.mkdir(parents=True, exist_ok=True)
    manifest_path = chat_root / HANDOFF_MANIFEST_NAME
    payload = {
        'version': 1,
        'scope': {'user_id': user_id, 'chat_id': chat_id},
        'entries': [
            {
                'original': entry['original'],
                'file_id': entry.get('file_id', ''),
                'sha256': entry.get('sha256', ''),
                'sig': entry['sig'],
            }
            for entry in entries
        ],
    }
    encoded_payload = json.dumps(
        payload,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    if len(encoded_payload) > HANDOFF_MANIFEST_MAX_BYTES:
        raise HandoffManifestCapacityError(
            f'handoff manifest exceeds {HANDOFF_MANIFEST_MAX_BYTES} bytes'
        )
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=chat_root,
            prefix='.owui-handoff-manifest-',
            suffix='.tmp',
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(encoded_payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, manifest_path)
        temp_path = None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _merge_manifest_entries(
    user_id: str,
    chat_id: str,
    new_entries: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Atomically read-modify-write one bounded chat manifest."""
    chat_root = _chat_handoff_dir(user_id, chat_id)
    chat_root.mkdir(parents=True, exist_ok=True)
    lock_path = chat_root / HANDOFF_MANIFEST_LOCK_NAME
    with FileLock(
        str(lock_path),
        timeout=HANDOFF_MANIFEST_LOCK_TIMEOUT_SECONDS,
    ):
        existing = _read_manifest_entries(user_id, chat_id)
        for entry in new_entries:
            entry = dict(entry)
            entry['sig'] = _sign_handoff_entry(
                user_id,
                chat_id,
                entry['original'],
                file_id=entry.get('file_id', ''),
                sha256=entry.get('sha256', ''),
            )
            identity = ('file_id', entry['file_id']) if entry.get('file_id') else (
                'original',
                entry['original'],
            )
            existing = [
                prior
                for prior in existing
                if (
                    ('file_id', prior.get('file_id'))
                    if prior.get('file_id')
                    else ('original', prior.get('original'))
                )
                != identity
            ]
            existing.append(entry)
        _atomic_write_manifest(user_id, chat_id, existing)
        return existing


def _cleanup_failed_turn_files(
    entries: list[dict[str, str]],
    subdir: Path,
) -> None:
    """Remove only files written by a turn whose manifest update failed."""
    for original in {entry['original'] for entry in entries}:
        path = Path(original)
        if path.parent != subdir:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning('hermes-handoff: failed to clean up %s: %s', path, exc)
    try:
        subdir.rmdir()
    except OSError:
        pass


def _load_manifest_entries(user_id: str, chat_id: str) -> list[dict[str, str]]:
    return _read_manifest_entries(user_id, chat_id)


def _build_materials_note(skipped: int) -> str:
    """#17 slice-4: build the COUNTS-ONLY ``<materials_note>`` appended to the injection when one
    or more current-turn uploads were SKIPPED (could not be resolved/written, so NOT delivered to
    the agent). The agent otherwise has no skip signal (the ``<files>`` block lists delivered files
    only), so this lets the work-product itself honestly state the limitation.

    M4: takes ONLY a count — it interpolates ``int(skipped)`` and NOTHING else, so a raw skipped
    filename / path / content can NEVER reach the note (the caller passes a number, never a name).
    Transient: injected into the FORWARDED prompt only, exactly like ``<files>`` — never persisted
    to ``webui.db``. Single source of the wording so it cannot drift. Scope = skipped/not-delivered
    files only; the low-extraction-confidence clause of AC#5 is unmeasurable for Path B (the agent
    reads raw bytes, no OWUI extraction) and is deferred to a future native-RAG slice."""
    n = int(skipped)
    return (
        f'<materials_note skipped="{n}">\n'
        f'{n} uploaded file(s) could not be read or delivered and were NOT considered in this answer.\n'
        f'If your response relies on the attached materials, briefly note that {n} file(s) were unavailable.\n'
        f"Do not guess these files' contents.\n"
        f'</materials_note>'
    )


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
      2. Resolve raw bytes for each file.
      3. Write original bytes into the shared handoff volume.
      4. Remove the processed file items from body['metadata']['files']
         (prevents OWUI native embedding / RAG injection).
      5. Inject a <files> block into the last user message.

    Returns
    -------
    body: the modified form_data body (file items removed from
          metadata.files; last user message has <files> block appended).
    """
    metadata = body.get('metadata')
    if not isinstance(metadata, dict):
        # Without metadata there is no user/chat scope to sign a handoff
        # against, so there is nothing safe to do. Say so instead of
        # half-working: the no-file branch below writes back into
        # body['metadata'], which would raise on a non-dict anyway.
        log.warning(
            'hermes-handoff: request metadata is %s, not a dict — skipping Path B',
            type(metadata).__name__,
        )
        return body

    # `.get(key, default)` only substitutes an ABSENT key, so a stored None
    # flowed into the list comprehensions below and aborted the whole handoff
    # with a TypeError; the caller then forwarded the turn with no file
    # context at all. `process_chat_payload` now normalises at the producer —
    # this is defence in depth for direct callers, and it normalises None
    # only. A non-list is a schema violation and must stay visible rather
    # than being coerced into a plausible-looking empty turn.
    files = _normalised_file_items(metadata.get('files'), source='metadata.files')

    user_id = _user_id_from_user(user)
    chat_id: str = _safe_segment(metadata.get('chat_id') or body.get('chat_id'), 'nochat')
    use_manifest = _manifest_enabled(metadata, user_id, chat_id)

    # Partition: current-turn file items vs. everything else. metadata.files
    # is chat-level and may contain historical file items, so it is not the
    # source of truth for "new upload on this turn" when user_message exists.
    file_items = _dedupe_file_items(_current_turn_file_items(metadata, files))
    other_items = [
        f for f in files
        if isinstance(f, dict) and f.get('type', 'file') != 'file'
    ]

    if not file_items:
        metadata['files'] = other_items
        if 'metadata' in body:
            body['metadata']['files'] = other_items
        entries = (
            await run_in_threadpool(_load_manifest_entries, user_id, chat_id)
            if use_manifest
            else []
        )
        if entries:
            _inject_transiently(body, _build_files_block(entries))
        # Positive signal for the no-upload turn. Without it the only evidence
        # that a follow-up turn carried file context was the ABSENCE of an
        # ERROR line, so a silent continuity loss looked exactly like a normal
        # fileless chat message.
        log.info(
            'hermes-handoff: Path B continuity — 0 new file(s), %d manifest '
            'entr%s reinjected (manifest=%s)',
            len(entries),
            'y' if len(entries) == 1 else 'ies',
            'on' if use_manifest else 'off',
        )
        return body

    # Determine the handoff subdir from user/chat/message context. User/chat
    # scope is also signed into each <file> entry and re-checked by Hermes.
    # Try to derive a message_id from the last user message (if present)
    message_id: Optional[str] = None
    for msg in reversed(body.get('messages', [])):
        if msg.get('role') == 'user':
            message_id = msg.get('id')
            break

    subdir = _make_handoff_subdir(user_id, chat_id, message_id)

    # #17 slice-3 keying fix: record the EXACT message-dir segment this handoff actually wrote
    # (``subdir.name``) into the request metadata, so the finalize-time partial-materials coverage
    # scan (utils/middleware.py + utils/file_coverage.py) reads the SAME dir instead of guessing
    # ``message/<user_message.id>/``. In the real flow ``body['messages']`` is id-stripped, so
    # ``message_id`` above is usually None and ``_make_handoff_subdir`` falls back to a random
    # uuid4 — which never matched ``user_message.id`` ⇒ the warning was inert. This does NOT change
    # the on-disk keying/layout or the agent's <files> paths; it only publishes the chosen segment.
    # ``metadata`` is the same dict threaded to the finalizer (process_chat_payload returns it →
    # ctx['metadata']); the defensive body write mirrors the metadata['files'] handling below.
    metadata['handoff_message_id'] = subdir.name
    if 'metadata' in body:
        body['metadata']['handoff_message_id'] = subdir.name

    turn_entries: list[dict[str, str]] = []

    for idx, file_item in enumerate(file_items, start=1):
        # Chat-completions often pass only {"type":"file","id":...} with no
        # name. Falling back to the literal "file" strips the extension and
        # breaks SOCv2 submit_conversion (unsupported extension ''). Prefer
        # the DB filename / meta.name / storage path basename when present.
        filename = (
            file_item.get('name')
            or file_item.get('filename')
            or file_item.get('file', {}).get('name')
            or file_item.get('file', {}).get('filename')
            or ''
        )
        file_id = _file_id_from_item(file_item)
        if (not filename or filename == 'file') and file_id:
            try:
                from open_webui.models.files import Files

                file_model = await Files.get_file_by_id(file_id)
                if file_model:
                    meta = file_model.meta or {}
                    if isinstance(meta, str):
                        import json as _json

                        try:
                            meta = _json.loads(meta)
                        except Exception:
                            meta = {}
                    filename = (
                        getattr(file_model, 'filename', None)
                        or (meta.get('name') if isinstance(meta, dict) else None)
                        or (
                            Path(file_model.path).name
                            if getattr(file_model, 'path', None)
                            else None
                        )
                        or filename
                    )
            except Exception as exc:
                log.warning(
                    'hermes-handoff: could not resolve filename for %s: %s',
                    file_id,
                    exc,
                )
        filename = filename or 'file'

        # 1. Resolve raw bytes
        raw_bytes = await _resolve_raw_bytes(file_item, filename)
        if not raw_bytes:
            log.warning('hermes-handoff: skipping %s — could not resolve bytes', filename)
            continue

        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        # 2. Write original bytes to shared handoff volume. Do NOT convert to
        # markdown here: Hermes/Origin Agent should receive the uploaded file
        # path and decide which tools to use for processing.
        try:
            orig_path = _write_handoff_files(subdir, filename, raw_bytes, idx)
        except OSError as exc:
            log.error(
                'hermes-handoff: failed to write %s to handoff volume %s: %s',
                filename, subdir, exc,
            )
            continue

        turn_entries.append(
            {
                'original': orig_path,
                'user_id': user_id,
                'chat_id': chat_id,
                'file_id': file_id,
                'sha256': content_sha256,
            }
        )
        log.info(
            'hermes-handoff: wrote %s → original=%s file_id=%s sha256=%s '
            '(no markdown conversion)',
            filename, orig_path, file_id or '-', content_sha256,
        )

    # 4. Remove processed file items from metadata.files (no embedding, no RAG)
    metadata['files'] = other_items
    if 'metadata' in body:
        body['metadata']['files'] = other_items

    # #17 slice-4: how many of THIS turn's uploads were skipped (could not be resolved/written →
    # NOT delivered to the agent). file_items is the deduped current-turn set; turn_entries is what was
    # actually handed off. Counts only — never names. (Reaching here implies file_items is non-empty;
    # the genuinely-no-uploaded-files turn already returned above, byte-for-byte unchanged.)
    skipped_count = len(file_items) - len(turn_entries)
    injection_entries = turn_entries

    if use_manifest:
        if turn_entries:
            try:
                injection_entries = await run_in_threadpool(
                    _merge_manifest_entries,
                    user_id,
                    chat_id,
                    turn_entries,
                )
            except (HandoffManifestCapacityError, FileLockTimeout) as exc:
                _cleanup_failed_turn_files(turn_entries, subdir)
                turn_entries = []
                skipped_count = len(file_items)
                injection_entries = await run_in_threadpool(
                    _load_manifest_entries,
                    user_id,
                    chat_id,
                )
                log.error(
                    'hermes-handoff: manifest unavailable; current turn marked skipped: %s',
                    exc,
                )
            except OSError as exc:
                log.error('hermes-handoff: failed to update chat manifest: %s', exc)
        else:
            injection_entries = await run_in_threadpool(
                _load_manifest_entries,
                user_id,
                chat_id,
            )

    # 5. Build the transient injection for the LAST USER message (NOT persisted to webui.db):
    #    - the <files> block (delivered files), when any were handed off; AND/OR
    #    - a counts-only <materials_note> when >=1 current-turn upload was skipped, so the agent can
    #      honestly state the materials limitation. An all-skipped turn injects any valid historical
    #      manifest entries plus the note; with no history it injects only the note.
    injection_parts: list[str] = []
    if injection_entries:
        injection_parts.append(_build_files_block(injection_entries))
    if skipped_count > 0:
        injection_parts.append(_build_materials_note(skipped_count))

    if not injection_parts:
        # No injected entries and nothing skipped is unreachable here; preserve a defensive no-op.
        log.warning('hermes-handoff: no files successfully written to handoff volume')
        return body

    _inject_transiently(body, '\n\n'.join(injection_parts))

    log.info(
        'hermes-handoff: Path B done — %d file(s) handed off under %s, %d skipped, '
        'file items removed from metadata.files (injected: %s)',
        len(turn_entries), subdir, skipped_count,
        '+'.join(
            [p for p in ('<files>' if injection_entries else '',
                         'materials_note' if skipped_count > 0 else '') if p]
        ),
    )

    return body

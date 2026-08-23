"""Append-only, content-addressed evidence for user journeys.

The active OpenWebUI tables are a projection: users may hide or delete rows.
This store preserves the exact pre-projection payload and uploaded bytes.  It
is deliberately filesystem-backed so a projection delete cannot cascade into
the evidence it describes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from open_webui.env import DATA_DIR

SCHEMA_VERSION = 1
EVIDENCE_DIR_ENV = 'OPENWEBUI_JOURNEY_EVIDENCE_DIR'


class EvidenceIntegrityError(RuntimeError):
    """The append-only evidence chain or one immutable object is invalid."""


def evidence_root() -> Path:
    configured = os.getenv(EVIDENCE_DIR_ENV, '').strip()
    return Path(configured) if configured else Path(DATA_DIR) / 'journey-evidence'


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def archive_bytes(content: bytes) -> dict[str, Any]:
    """Store exact bytes once and verify any existing object byte-for-byte."""
    digest = hashlib.sha256(content).hexdigest()
    root = evidence_root()
    object_dir = root / 'objects' / digest[:2]
    _ensure_private_directory(object_dir)
    object_path = object_dir / digest

    try:
        fd = os.open(object_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = object_path.read_bytes()
        if len(existing) != len(content) or hashlib.sha256(existing).hexdigest() != digest:
            raise EvidenceIntegrityError(f'immutable evidence object mismatch: {digest}')
    else:
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            # A partial object is not valid evidence. The exact target is safe
            # to unlink because this process created it with O_EXCL and has not
            # returned or journaled it.
            try:
                object_path.unlink()
            except OSError:
                pass
            raise

    return {'sha256': digest, 'size': len(content)}


def archive_json(payload: Any) -> dict[str, Any]:
    return archive_bytes(_canonical_json(payload))


def _event_hash(event_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(event_without_hash)).hexdigest()


def append_event(
    event_type: str,
    *,
    subject_type: str,
    subject_id: str,
    actor: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    """Append one tamper-evident event whose full payload is immutable."""
    payload_object = archive_json(payload)
    root = evidence_root()
    _ensure_private_directory(root)
    journal = root / 'events.jsonl'
    fd = os.open(journal, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        with os.fdopen(fd, 'r+b', buffering=0) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            lines = [line for line in handle.read().splitlines() if line.strip()]
            previous_event_hash = None
            if lines:
                try:
                    previous_event_hash = json.loads(lines[-1])['event_hash']
                except (KeyError, TypeError, ValueError) as exc:
                    raise EvidenceIntegrityError('journal tail is not a valid evidence event') from exc

            event = {
                'schema': SCHEMA_VERSION,
                'event_id': str(uuid.uuid4()),
                'recorded_at_ns': time.time_ns(),
                'event_type': event_type,
                'subject_type': subject_type,
                'subject_id': str(subject_id),
                'actor': actor,
                'payload_object': payload_object,
                'previous_event_hash': previous_event_hash,
            }
            event['event_hash'] = _event_hash(event)
            handle.seek(0, os.SEEK_END)
            handle.write(_canonical_json(event) + b'\n')
            os.fsync(handle.fileno())
            return event
    finally:
        # fdopen owns the descriptor on normal and exceptional exits.
        pass


def _read_object(reference: dict[str, Any]) -> bytes:
    digest = str(reference.get('sha256') or '')
    expected_size = int(reference.get('size') or -1)
    if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
        raise EvidenceIntegrityError('invalid evidence object digest')
    object_path = evidence_root() / 'objects' / digest[:2] / digest
    content = object_path.read_bytes()
    if len(content) != expected_size or hashlib.sha256(content).hexdigest() != digest:
        raise EvidenceIntegrityError(f'evidence object verification failed: {digest}')
    return content


def _verify_nested_object_references(value: Any) -> None:
    if isinstance(value, dict):
        if set(value) == {'sha256', 'size'}:
            _read_object(value)
            return
        for nested in value.values():
            _verify_nested_object_references(nested)
    elif isinstance(value, list):
        for nested in value:
            _verify_nested_object_references(nested)


def read_subject(subject_type: str, subject_id: str) -> list[dict[str, Any]]:
    """Verify the complete chain and reconstruct one authorized subject."""
    journal = evidence_root() / 'events.jsonl'
    if not journal.exists():
        return []
    with journal.open('rb') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        raw_lines = handle.read().splitlines()
    previous_event_hash = None
    matches: list[dict[str, Any]] = []
    for raw_line in raw_lines:
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except ValueError as exc:
            raise EvidenceIntegrityError('journal contains invalid JSON') from exc
        claimed_hash = event.pop('event_hash', None)
        if event.get('previous_event_hash') != previous_event_hash:
            raise EvidenceIntegrityError('journal chain is discontinuous')
        actual_hash = _event_hash(event)
        if claimed_hash != actual_hash:
            raise EvidenceIntegrityError('journal event hash mismatch')
        previous_event_hash = actual_hash
        event['event_hash'] = claimed_hash
        if event.get('subject_type') == subject_type and str(event.get('subject_id')) == str(subject_id):
            payload = json.loads(_read_object(event['payload_object']))
            _verify_nested_object_references(payload)
            matches.append({**event, 'payload': payload})
    return matches

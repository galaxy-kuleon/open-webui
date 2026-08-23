"""OpenWebUI projection hooks for the append-only journey evidence store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile, ChatModel
from open_webui.models.files import File as FileRecord
from open_webui.models.files import FileModel
from open_webui.storage.provider import Storage
from open_webui.utils.journey_evidence import EvidenceIntegrityError, append_event, archive_bytes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def actor_record(user: Any) -> dict[str, Any]:
    return {
        'id': getattr(user, 'id', None),
        'email': getattr(user, 'email', None),
        'name': getattr(user, 'name', None),
        'role': getattr(user, 'role', None),
    }


def request_record(request: Any) -> dict[str, Any]:
    headers = getattr(request, 'headers', {})
    client = getattr(request, 'client', None)
    return {
        'method': getattr(request, 'method', None),
        'path': str(getattr(getattr(request, 'url', None), 'path', '')),
        'client_host': getattr(client, 'host', None),
        'user_agent': headers.get('user-agent'),
        'x_request_id': headers.get('x-request-id'),
        'x_correlation_id': headers.get('x-correlation-id'),
    }


def _row_record(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _archive_stored_file(
    file: FileModel, *, raw_record: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not file.path:
        raise EvidenceIntegrityError(f'uploaded file has no storage path: {file.id}')
    resolved = Path(Storage.get_file(file.path))
    content = resolved.read_bytes()
    reference = archive_bytes(content)
    return {
        'record': raw_record if raw_record is not None else file.model_dump(mode='json'),
        'bytes': reference,
        'stored_hash_matches_bytes': file.hash in {None, reference['sha256']},
    }


def record_upload_accepted(
    *,
    file: FileModel,
    content: bytes,
    metadata: dict[str, Any],
    actor: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    reference = archive_bytes(content)
    if file.hash != reference['sha256']:
        raise EvidenceIntegrityError(f'accepted upload hash mismatch: {file.id}')
    return append_event(
        'upload.accepted',
        subject_type='file',
        subject_id=file.id,
        actor=actor,
        payload={
            'file': file.model_dump(mode='json'),
            'bytes': reference,
            'upload_metadata': metadata,
            'request': request,
        },
    )


async def archive_file_delete_request(
    *,
    db: AsyncSession,
    file: FileModel,
    actor: dict[str, Any],
    request: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return append_event(
        'projection.delete.requested',
        subject_type='file',
        subject_id=file.id,
        actor=actor,
        payload={
            'reason': reason,
            'request': request,
            'before': _archive_stored_file(file),
            'after': {'visible': False},
        },
    )


async def archive_chat_delete_request(
    *,
    db: AsyncSession,
    chat: ChatModel,
    actor: dict[str, Any],
    request: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    chat_row = await db.get(Chat, chat.id)
    if not chat_row:
        raise EvidenceIntegrityError(f'chat disappeared before evidence capture: {chat.id}')
    message_rows = (
        await db.execute(
            select(ChatMessage)
            .filter_by(chat_id=chat.id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        )
    ).scalars().all()
    link_rows = (
        await db.execute(
            select(ChatFile)
            .filter_by(chat_id=chat.id)
            .order_by(ChatFile.created_at.asc(), ChatFile.id.asc())
        )
    ).scalars().all()
    file_ids = sorted({row.file_id for row in link_rows})
    file_rows = []
    if file_ids:
        file_rows = (
            await db.execute(select(FileRecord).filter(FileRecord.id.in_(file_ids)))
        ).scalars().all()
        observed_ids = {row.id for row in file_rows}
        missing = sorted(set(file_ids) - observed_ids)
        if missing:
            raise EvidenceIntegrityError(
                f'chat upload rows are missing before delete: {chat.id}: {missing}'
            )

    snapshot = {
        'chat': _row_record(chat_row),
        'messages': [_row_record(row) for row in message_rows],
        'chat_files': [_row_record(row) for row in link_rows],
        'files': [
            _archive_stored_file(
                FileModel.model_validate(row), raw_record=_row_record(row)
            )
            for row in sorted(file_rows, key=lambda item: item.id)
        ],
    }
    return append_event(
        'projection.delete.requested',
        subject_type='chat',
        subject_id=chat.id,
        actor=actor,
        payload={
            'reason': reason,
            'request': request,
            'before': snapshot,
            'after': {'visible': False},
        },
    )


def record_projection_delete_outcome(
    *,
    subject_type: str,
    subject_id: str,
    actor: dict[str, Any],
    request_event_id: str,
    visible: bool,
    result: str,
) -> dict[str, Any]:
    return append_event(
        f'projection.delete.{result}',
        subject_type=subject_type,
        subject_id=subject_id,
        actor=actor,
        payload={
            'request_event_id': request_event_id,
            'after': {'visible': visible},
        },
    )


async def get_raw_chat(db: AsyncSession, chat_id: str) -> ChatModel | None:
    row = await db.get(Chat, chat_id)
    return ChatModel.model_validate(row) if row else None


async def get_raw_chats_by_user(db: AsyncSession, user_id: str) -> list[ChatModel]:
    rows = (
        await db.execute(
            select(Chat).filter_by(user_id=user_id).order_by(Chat.created_at.asc(), Chat.id.asc())
        )
    ).scalars().all()
    return [ChatModel.model_validate(row) for row in rows]


async def get_raw_chats_by_folder(
    db: AsyncSession, user_id: str, folder_id: str
) -> list[ChatModel]:
    rows = (
        await db.execute(
            select(Chat)
            .filter_by(user_id=user_id, folder_id=folder_id)
            .order_by(Chat.created_at.asc(), Chat.id.asc())
        )
    ).scalars().all()
    return [ChatModel.model_validate(row) for row in rows]

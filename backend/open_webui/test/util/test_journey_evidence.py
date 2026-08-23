import hashlib
import json
from pathlib import Path

import pytest
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, ChatFile, ChatModel
from open_webui.models.files import File
from open_webui.routers.files import _verified_upload_hash
from open_webui.utils.journey_archive import (
    archive_chat_delete_request,
    record_projection_delete_outcome,
)
from open_webui.utils.journey_evidence import (
    EvidenceIntegrityError,
    append_event,
    archive_bytes,
    read_subject,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_upload_hash_is_server_computed_and_mismatch_fails_closed():
    content = b'exact original upload bytes'
    server_hash = hashlib.sha256(content).hexdigest()

    assert _verified_upload_hash(content) == server_hash
    assert _verified_upload_hash(content, server_hash.upper()) == server_hash
    with pytest.raises(ValueError, match='do not match'):
        _verified_upload_hash(content, '0' * 64)
    with pytest.raises(ValueError, match='do not match'):
        _verified_upload_hash(content, 'not-a-sha256')


def test_append_only_subject_reconstructs_exact_payload_and_tombstone(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENWEBUI_JOURNEY_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    raw_chat = {
        'chat': {'id': 'chat-1', 'title': '商標案件', 'history': {'messages': {'m1': {'content': '完整原文'}}}},
        'messages': [{'id': 'm1', 'content': '完整原文'}],
        'files': [],
    }
    requested = append_event(
        'projection.delete.requested',
        subject_type='chat',
        subject_id='chat-1',
        actor={'id': 'user-1', 'role': 'user'},
        payload={'before': raw_chat, 'after': {'visible': False}, 'reason': 'user_requested'},
    )
    append_event(
        'projection.delete.completed',
        subject_type='chat',
        subject_id='chat-1',
        actor={'id': 'user-1', 'role': 'user'},
        payload={'request_event_id': requested['event_id'], 'after': {'visible': False}},
    )

    events = read_subject('chat', 'chat-1')

    assert [event['event_type'] for event in events] == [
        'projection.delete.requested',
        'projection.delete.completed',
    ]
    assert events[0]['payload']['before'] == raw_chat
    assert events[0]['payload']['after'] == {'visible': False}
    assert events[1]['payload']['request_event_id'] == requested['event_id']


def test_content_addressed_object_is_not_overwritten_and_tampering_is_detected(tmp_path, monkeypatch):
    root = tmp_path / 'evidence'
    monkeypatch.setenv('OPENWEBUI_JOURNEY_EVIDENCE_DIR', str(root))
    content = b'original bytes\x00remain exact'
    first = archive_bytes(content)
    second = archive_bytes(content)

    assert second == first
    object_path = root / 'objects' / first['sha256'][:2] / first['sha256']
    assert object_path.read_bytes() == content

    append_event(
        'upload.accepted',
        subject_type='file',
        subject_id='file-1',
        actor={'id': 'user-1'},
        payload={'bytes': first},
    )
    object_path.write_bytes(b'tampered')

    with pytest.raises(EvidenceIntegrityError, match='verification failed'):
        read_subject('file', 'file-1')


def test_journal_chain_detects_event_rewrite(tmp_path, monkeypatch):
    root = tmp_path / 'evidence'
    monkeypatch.setenv('OPENWEBUI_JOURNEY_EVIDENCE_DIR', str(root))
    append_event(
        'upload.accepted',
        subject_type='file',
        subject_id='file-1',
        actor={'id': 'user-1'},
        payload={'filename': 'original.pdf'},
    )
    journal = root / 'events.jsonl'
    event = json.loads(journal.read_text(encoding='utf-8'))
    event['subject_id'] = 'rewritten'
    journal.write_text(json.dumps(event) + '\n', encoding='utf-8')

    with pytest.raises(EvidenceIntegrityError, match='hash mismatch'):
        read_subject('file', 'file-1')


def test_delete_routes_are_wired_to_archive_before_projection():
    backend = Path(__file__).resolve().parents[3]
    chats_source = (backend / 'open_webui/routers/chats.py').read_text(encoding='utf-8')
    files_source = (backend / 'open_webui/routers/files.py').read_text(encoding='utf-8')

    assert chats_source.index('archive_chat_delete_request(') < chats_source.index(
        'Chats.delete_chats_by_user_id(user.id'
    )
    individual = chats_source.index("@router.delete('/{id}', response_model=bool)")
    assert chats_source.index('archive_chat_delete_request(', individual) < chats_source.index(
        'Chats.delete_chat_by_id(', individual
    )
    upload = files_source.index('async def upload_file_handler(')
    assert files_source.index('_verified_upload_hash(', upload) < files_source.index(
        'Files.insert_new_file(', upload
    )
    delete = files_source.index("@router.delete('/{id}')")
    assert files_source.index('archive_file_delete_request(', delete) < files_source.index(
        'Files.delete_file_by_id(', delete
    )


@pytest.mark.asyncio
async def test_real_projection_delete_retains_exact_chat_messages_links_and_bytes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv('OPENWEBUI_JOURNEY_EVIDENCE_DIR', str(tmp_path / 'evidence'))
    upload = tmp_path / 'original.pdf'
    upload.write_bytes(b'%PDF exact original bytes')
    digest = hashlib.sha256(upload.read_bytes()).hexdigest()
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Chat.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: File.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: ChatMessage.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: ChatFile.__table__.create(sync, checkfirst=True)
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        chat_row = Chat(
            id='chat-db-1',
            user_id='user-1',
            title='原始案件',
            chat={'history': {'messages': {'m1': {'role': 'user', 'content': '原始問題'}}}},
            created_at=100,
            updated_at=101,
            archived=False,
            pinned=False,
            meta={},
        )
        session.add_all(
            [
                chat_row,
                File(
                    id='file-db-1',
                    user_id='user-1',
                    hash=digest,
                    filename='original.pdf',
                    path=str(upload),
                    data={'status': 'completed'},
                    meta={'size': upload.stat().st_size},
                    created_at=100,
                    updated_at=101,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ChatMessage(
                    id='chat-db-1-m1',
                    chat_id='chat-db-1',
                    user_id='user-1',
                    role='user',
                    content='原始問題',
                    done=True,
                    created_at=100,
                    updated_at=101,
                ),
                ChatFile(
                    id='link-1',
                    user_id='user-1',
                    chat_id='chat-db-1',
                    message_id='m1',
                    file_id='file-db-1',
                    created_at=100,
                    updated_at=101,
                ),
            ]
        )
        await session.commit()

        requested = await archive_chat_delete_request(
            db=session,
            chat=ChatModel.model_validate(chat_row),
            actor={'id': 'user-1', 'role': 'user'},
            request={'method': 'DELETE', 'path': '/api/v1/chats/chat-db-1'},
            reason='user_requested',
        )
        await session.execute(delete(ChatMessage).filter_by(chat_id='chat-db-1'))
        await session.execute(delete(Chat).filter_by(id='chat-db-1'))
        await session.commit()
        record_projection_delete_outcome(
            subject_type='chat',
            subject_id='chat-db-1',
            actor={'id': 'user-1', 'role': 'user'},
            request_event_id=requested['event_id'],
            visible=False,
            result='completed',
        )

        assert await session.get(Chat, 'chat-db-1') is None
        events = read_subject('chat', 'chat-db-1')
        before = events[0]['payload']['before']
        assert before['chat']['chat']['history']['messages']['m1']['content'] == '原始問題'
        assert before['messages'][0]['content'] == '原始問題'
        assert before['chat_files'][0]['file_id'] == 'file-db-1'
        assert before['files'][0]['bytes'] == {
            'sha256': digest,
            'size': upload.stat().st_size,
        }
        assert events[1]['payload']['after'] == {'visible': False}

    await engine.dispose()

import pytest
import pytest_asyncio
from fastapi import HTTPException
from open_webui.models.feedbacks import (
    FEEDBACK_CATEGORY_KEYS,
    Feedback,
    FeedbackForm,
    Feedbacks,
)
from open_webui.routers import evaluations as evaluations_router
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture()
async def db_sessionmaker():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Feedback.__table__.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_feedback_form_persists_qa_taxonomy_and_binding_metadata(db_sessionmaker):
    form = FeedbackForm(
        type='rating',
        data={
            'rating': -1,
            'rating_signal': 'negative',
            'model_id': 'legal-model',
            'comment': 'Formatting broke the exported letter.',
            'free_text': 'Formatting broke the exported letter.',
            'categories': [
                'download_or_export_failed',
                'formatting_or_layout_problem',
                'not_client_ready',
                'download_or_export_failed',
            ],
            'artifacts': [
                {
                    'source': 'markdown_link',
                    'label': 'Download DOCX',
                    'url': '/v1/artifacts/a1/output.docx/download/1893456000/sig',
                }
            ],
        },
        meta={
            'chat_id': 'chat-1',
            'message_id': 'message-1',
            'model_id': 'legal-model',
        },
    )

    async with db_sessionmaker() as session:
        feedback = await Feedbacks.insert_new_feedback('alice', form, db=session)

    assert feedback.user_id == 'alice'
    assert feedback.type == 'rating'
    assert feedback.data['rating_signal'] == 'negative'
    assert feedback.data['categories'] == [
        'download_or_export_failed',
        'formatting_or_layout_problem',
        'not_client_ready',
    ]
    assert feedback.data['artifacts'][0]['source'] == 'markdown_link'
    assert feedback.meta == {
        'chat_id': 'chat-1',
        'message_id': 'message-1',
        'model_id': 'legal-model',
    }


def test_feedback_form_rejects_unknown_qa_categories():
    with pytest.raises(ValidationError, match='unsupported feedback categories'):
        FeedbackForm(
            type='rating',
            data={
                'rating': -1,
                'categories': ['download_or_export_failed', 'not_a_real_category'],
            },
        )


def test_feedback_form_rejects_overlong_free_text():
    with pytest.raises(ValidationError):
        FeedbackForm(
            type='rating',
            data={
                'rating': -1,
                'free_text': 'x' * 4001,
            },
        )


def test_feedback_form_accepts_every_supported_qa_category():
    form = FeedbackForm(
        type='rating',
        data={
            'rating': 1,
            'rating_signal': 'positive',
            'categories': list(FEEDBACK_CATEGORY_KEYS),
        },
    )

    assert form.data.categories == list(FEEDBACK_CATEGORY_KEYS)


@pytest.mark.asyncio
async def test_feedback_binding_rejects_non_owner_chat_id(monkeypatch):
    async def fake_is_chat_owner(chat_id, user_id, db=None):
        return False

    monkeypatch.setattr(evaluations_router.Chats, 'is_chat_owner', fake_is_chat_owner)

    form = FeedbackForm(type='rating', data={'rating': -1}, meta={'chat_id': 'other-chat'})
    user = type('User', (), {'id': 'alice', 'role': 'user'})()

    with pytest.raises(HTTPException) as exc:
        await evaluations_router._validate_feedback_binding(form, user, db=None)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_feedback_binding_accepts_owner_chat_id(monkeypatch):
    async def fake_is_chat_owner(chat_id, user_id, db=None):
        return chat_id == 'chat-1' and user_id == 'alice'

    monkeypatch.setattr(evaluations_router.Chats, 'is_chat_owner', fake_is_chat_owner)

    form = FeedbackForm(type='rating', data={'rating': -1}, meta={'chat_id': 'chat-1'})
    user = type('User', (), {'id': 'alice', 'role': 'user'})()

    await evaluations_router._validate_feedback_binding(form, user, db=None)


@pytest.mark.asyncio
async def test_feedback_binding_rejects_unknown_message_id(monkeypatch):
    async def fake_is_chat_owner(chat_id, user_id, db=None):
        return True

    async def fake_get_chat_by_id_and_user_id(chat_id, user_id, db=None):
        return type(
            'Chat',
            (),
            {
                'chat': {
                    'history': {
                        'messages': {
                            'known-message': {'id': 'known-message'},
                        }
                    }
                }
            },
        )()

    monkeypatch.setattr(evaluations_router.Chats, 'is_chat_owner', fake_is_chat_owner)
    monkeypatch.setattr(evaluations_router.Chats, 'get_chat_by_id_and_user_id', fake_get_chat_by_id_and_user_id)

    form = FeedbackForm(
        type='rating',
        data={'rating': -1},
        meta={'chat_id': 'chat-1', 'message_id': 'missing-message'},
    )
    user = type('User', (), {'id': 'alice', 'role': 'user'})()

    with pytest.raises(HTTPException) as exc:
        await evaluations_router._validate_feedback_binding(form, user, db=None)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_feedback_binding_allows_unpersisted_current_assistant_message(monkeypatch):
    async def fake_is_chat_owner(chat_id, user_id, db=None):
        return True

    async def fake_get_chat_by_id_and_user_id(chat_id, user_id, db=None):
        return type(
            'Chat',
            (),
            {
                'chat': {
                    'history': {
                        'messages': {
                            'user-message': {
                                'id': 'user-message',
                                'role': 'user',
                                'childrenIds': [],
                            },
                        }
                    }
                }
            },
        )()

    monkeypatch.setattr(evaluations_router.Chats, 'is_chat_owner', fake_is_chat_owner)
    monkeypatch.setattr(evaluations_router.Chats, 'get_chat_by_id_and_user_id', fake_get_chat_by_id_and_user_id)

    form = FeedbackForm(
        type='rating',
        data={'rating': -1},
        meta={
            'chat_id': 'chat-1',
            'message_id': 'assistant-message-not-saved-yet',
            'parent_message_id': 'user-message',
            'message_role': 'assistant',
        },
    )
    user = type('User', (), {'id': 'alice', 'role': 'user'})()

    await evaluations_router._validate_feedback_binding(form, user, db=None)
    assert form.meta['message_id_validation'] == 'message_id_validation_skipped_race_bypass'

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import open_webui.config as openwebui_config
from open_webui.models.feedbacks import Feedback, FeedbackForm, Feedbacks
from open_webui.models.feedback_outbox import FeedbackOutbox, FeedbackOutboxes
from open_webui.routers import evaluations as evaluations_router
from open_webui.routers import memories as memories_router
from open_webui.utils.hermes_bridge import (
    HermesBridgeClient,
    HermesBridgeConfig,
    HermesMalformedMemoryResponseError,
    HermesMemoryListPage,
    build_hermes_headers,
    build_memory_search_result,
    get_bridge_config,
    memory_bridge_enabled,
    memory_bridge_misconfigured,
    memory_model_from_bridge,
    outbox_drain_enabled,
    feedback_delete_purge_enabled,
    feedback_outbox_capture_enabled,
    outbox_worker_enabled,
    should_bypass_openwebui_memory_injection,
)
from open_webui.utils.hermes_outbox import drain_feedback_outbox_once


@pytest.mark.parametrize(
    'env_name',
    [
        'OPENWEBUI_HERMES_BRIDGE_ENABLED',
        'OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED',
        'OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED',
        'OPENWEBUI_HERMES_DELETE_PURGE_ENABLED',
    ],
)
@pytest.mark.parametrize('truthy_value', ['1', 'true', 'TRUE', 'yes', 'YES', 'on', 'ON'])
def test_config_hermes_env_flags_accept_bridge_truthy_forms(monkeypatch, env_name, truthy_value):
    monkeypatch.setenv(env_name, truthy_value)

    assert openwebui_config._env_bool(env_name, default=False) is True


def test_config_hermes_env_flags_set_runtime_globals_in_fresh_import(tmp_path):
    backend_dir = Path(__file__).resolve().parents[3]
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            'DATA_DIR': str(data_dir),
            'OPENWEBUI_HERMES_BRIDGE_ENABLED': 'yes',
            'OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED': 'on',
            'OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED': '1',
            'OPENWEBUI_HERMES_DELETE_PURGE_ENABLED': 'true',
        }
    )
    env['PYTHONPATH'] = (
        str(backend_dir)
        if not env.get('PYTHONPATH')
        else f"{backend_dir}{os.pathsep}{env['PYTHONPATH']}"
    )
    code = (
        "import json\n"
        "import open_webui.config as c\n"
        "print(json.dumps({\n"
        "    'bridge': c.HERMES_BRIDGE_ENABLED.value,\n"
        "    'memory': c.HERMES_MEMORY_BRIDGE_ENABLED.value,\n"
        "    'outbox': c.HERMES_OUTBOX_DRAIN_ENABLED.value,\n"
        "    'delete_purge': c.HERMES_DELETE_PURGE_ENABLED.value,\n"
        "}))\n"
    )

    result = subprocess.run(
        [sys.executable, '-c', code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        'bridge': True,
        'memory': True,
        'outbox': True,
        'delete_purge': True,
    }


def test_config_hermes_settings_register_in_app_config_without_key_error():
    config = openwebui_config.AppConfig()

    config.HERMES_BRIDGE_ENABLED = openwebui_config.HERMES_BRIDGE_ENABLED
    config.HERMES_MEMORY_BRIDGE_ENABLED = openwebui_config.HERMES_MEMORY_BRIDGE_ENABLED
    config.HERMES_OUTBOX_DRAIN_ENABLED = openwebui_config.HERMES_OUTBOX_DRAIN_ENABLED
    config.HERMES_DELETE_PURGE_ENABLED = openwebui_config.HERMES_DELETE_PURGE_ENABLED
    config.HERMES_BRIDGE_URL = openwebui_config.HERMES_BRIDGE_URL
    config.HERMES_BRIDGE_API_KEY = openwebui_config.HERMES_BRIDGE_API_KEY
    config.HERMES_BRIDGE_TIMEOUT = openwebui_config.HERMES_BRIDGE_TIMEOUT

    assert config.HERMES_BRIDGE_ENABLED is openwebui_config.HERMES_BRIDGE_ENABLED.value
    assert config.HERMES_MEMORY_BRIDGE_ENABLED is openwebui_config.HERMES_MEMORY_BRIDGE_ENABLED.value
    assert config.HERMES_OUTBOX_DRAIN_ENABLED is openwebui_config.HERMES_OUTBOX_DRAIN_ENABLED.value
    assert config.HERMES_DELETE_PURGE_ENABLED is openwebui_config.HERMES_DELETE_PURGE_ENABLED.value
    assert config.HERMES_BRIDGE_URL == openwebui_config.HERMES_BRIDGE_URL.value
    assert config.HERMES_BRIDGE_API_KEY == openwebui_config.HERMES_BRIDGE_API_KEY.value
    assert config.HERMES_BRIDGE_TIMEOUT == openwebui_config.HERMES_BRIDGE_TIMEOUT.value


@pytest_asyncio.fixture()
async def db_sessionmaker():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Feedback.__table__.create)
        await conn.run_sync(FeedbackOutbox.__table__.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_feedback_mutation_and_outbox_commit_atomically(db_sessionmaker):
    async with db_sessionmaker() as session:
        with pytest.raises(RuntimeError):
            async with session.begin():
                await Feedbacks.insert_new_feedback_in_session(
                    user_id='alice',
                    form_data=FeedbackForm(type='rating', data={'rating': 1}),
                    db=session,
                )
                raise RuntimeError('fail before outbox enqueue')

    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Feedback)) == 0
        assert await session.scalar(select(func.count()).select_from(FeedbackOutbox)) == 0

    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Feedback)) == 1
        assert await session.scalar(select(func.count()).select_from(FeedbackOutbox)) == 1


@pytest.mark.asyncio
async def test_delete_feedback_outbox_event_is_metadata_only(db_sessionmaker):
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}, meta={'chat_id': 'c1'}),
                db=session,
            )

        async with session.begin():
            deleted_snapshot = await Feedbacks.delete_feedback_by_id_and_user_id_in_session(
                id=feedback.id,
                user_id='alice',
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='deleted',
                feedback=deleted_snapshot,
                actor_user_id='alice',
                actor_role='user',
            )

        result = await session.execute(select(FeedbackOutbox).where(FeedbackOutbox.event_type == 'deleted'))
        row = result.scalars().one()
        assert row.payload['feedback_id'] == feedback.id
        assert row.payload['version'] == 1
        assert 'feedback' not in row.payload
        assert 'previous_feedback' not in row.payload


@pytest.mark.asyncio
async def test_feedback_outbox_versions_order_create_update_delete(db_sessionmaker):
    async with db_sessionmaker() as session:
        async with session.begin():
            created = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1, 'comment': 'initial'}),
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=created,
                actor_user_id='alice',
                actor_role='user',
            )

        async with session.begin():
            previous = await Feedbacks.get_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                db=session,
            )
            updated = await Feedbacks.update_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': -1, 'comment': 'updated'}),
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='updated',
                feedback=updated,
                actor_user_id='alice',
                actor_role='user',
                previous=previous,
            )

        async with session.begin():
            deleted = await Feedbacks.delete_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='deleted',
                feedback=deleted,
                actor_user_id='alice',
                actor_role='user',
            )

        result = await session.execute(select(FeedbackOutbox).order_by(FeedbackOutbox.created_at, FeedbackOutbox.event_id))
        rows = result.scalars().all()

    assert [row.event_type for row in rows] == ['created', 'updated', 'deleted']
    assert [row.event_id for row in rows] == [
        f'feedback:{created.id}:0:created',
        f'feedback:{created.id}:1:updated',
        f'feedback:{created.id}:2:deleted',
    ]
    assert [row.payload['version'] for row in rows] == [0, 1, 2]
    assert [row.feedback_version for row in rows] == [0, 1, 2]
    assert rows[1].payload['previous_feedback']['version'] == 0
    assert rows[1].payload['previous_feedback']['data']['comment'] == 'initial'
    assert rows[1].payload['feedback']['data']['comment'] == 'updated'


@pytest.mark.asyncio
async def test_feedback_update_version_increment_uses_current_database_value(db_sessionmaker):
    async with db_sessionmaker() as session:
        async with session.begin():
            created = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1, 'comment': 'initial'}),
                db=session,
            )

    async with db_sessionmaker() as first_reader:
        stale_first = await Feedbacks.get_feedback_by_id_and_user_id_in_session(
            id=created.id,
            user_id='alice',
            db=first_reader,
        )
    async with db_sessionmaker() as second_reader:
        stale_second = await Feedbacks.get_feedback_by_id_and_user_id_in_session(
            id=created.id,
            user_id='alice',
            db=second_reader,
        )

    assert stale_first.version == 0
    assert stale_second.version == 0

    async with db_sessionmaker() as session:
        async with session.begin():
            first_update = await Feedbacks.update_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': -1, 'comment': 'first'}),
                db=session,
            )
    async with db_sessionmaker() as session:
        async with session.begin():
            second_update = await Feedbacks.update_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1, 'comment': 'second'}),
                db=session,
            )

    assert first_update.version == 1
    assert second_update.version == 2


@pytest.mark.asyncio
async def test_bulk_feedback_delete_can_enqueue_one_delete_event_per_snapshot(db_sessionmaker):
    async with db_sessionmaker() as session:
        async with session.begin():
            for rating in (1, -1):
                await Feedbacks.insert_new_feedback_in_session(
                    user_id='alice',
                    form_data=FeedbackForm(type='rating', data={'rating': rating}),
                    db=session,
                )

        async with session.begin():
            deleted = await Feedbacks.delete_feedbacks_by_user_id_in_session(user_id='alice', db=session)
            for feedback in deleted:
                await FeedbackOutboxes.enqueue_feedback_event(
                    session,
                    event_type='deleted',
                    feedback=feedback,
                    actor_user_id='alice',
                    actor_role='user',
                )

        assert len(deleted) == 2
        assert await session.scalar(select(func.count()).select_from(Feedback)) == 0
        assert await session.scalar(select(func.count()).select_from(FeedbackOutbox)) == 2


@pytest.mark.asyncio
async def test_feedback_outbox_retry_waits_until_next_attempt(db_sessionmaker, monkeypatch):
    import open_webui.internal.db as internal_db

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            event = await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        claimed = await FeedbackOutboxes.claim_batch(now=100, db=session)
        assert [row.event_id for row in claimed] == [event.event_id]
        await FeedbackOutboxes.mark_failed(event.event_id, 'temporary hermes outage', now=100, db=session)

        assert await FeedbackOutboxes.claim_batch(now=101, db=session) == []
        assert [row.event_id for row in await FeedbackOutboxes.claim_batch(now=103, db=session)] == [event.event_id]


@pytest.mark.asyncio
async def test_feedback_outbox_claim_orders_same_second_events_by_feedback_version(db_sessionmaker, monkeypatch):
    import open_webui.internal.db as internal_db

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    async with db_sessionmaker() as session:
        session.add_all(
            [
                FeedbackOutbox(
                    id='feedback:f1:2:deleted',
                    event_id='feedback:f1:2:deleted',
                    feedback_id='f1',
                    feedback_version=2,
                    user_id='alice',
                    event_type='deleted',
                    payload={'version': 2},
                    status='pending',
                    attempts=0,
                    next_attempt_at=0,
                    lease_until=0,
                    created_at=100,
                    updated_at=100,
                ),
                FeedbackOutbox(
                    id='feedback:f1:0:created',
                    event_id='feedback:f1:0:created',
                    feedback_id='f1',
                    feedback_version=0,
                    user_id='alice',
                    event_type='created',
                    payload={'version': 0},
                    status='pending',
                    attempts=0,
                    next_attempt_at=0,
                    lease_until=0,
                    created_at=100,
                    updated_at=100,
                ),
                FeedbackOutbox(
                    id='feedback:f1:1:updated',
                    event_id='feedback:f1:1:updated',
                    feedback_id='f1',
                    feedback_version=1,
                    user_id='alice',
                    event_type='updated',
                    payload={'version': 1},
                    status='pending',
                    attempts=0,
                    next_attempt_at=0,
                    lease_until=0,
                    created_at=100,
                    updated_at=100,
                ),
            ]
        )
        await session.commit()

        claimed = await FeedbackOutboxes.claim_batch(now=100, db=session)

    assert [row.event_id for row in claimed] == [
        'feedback:f1:0:created',
        'feedback:f1:1:updated',
        'feedback:f1:2:deleted',
    ]


@pytest.mark.asyncio
async def test_feedback_delete_redacts_prior_outbox_payloads(db_sessionmaker):
    async with db_sessionmaker() as session:
        async with session.begin():
            created = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1, 'comment': 'private'}),
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=created,
                actor_user_id='alice',
                actor_role='user',
            )

        async with session.begin():
            deleted = await Feedbacks.delete_feedback_by_id_and_user_id_in_session(
                id=created.id,
                user_id='alice',
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='deleted',
                feedback=deleted,
                actor_user_id='alice',
                actor_role='user',
            )
            await FeedbackOutboxes.redact_feedback_payloads(
                session,
                feedback_id=created.id,
                reason='feedback_deleted',
            )

        result = await session.execute(select(FeedbackOutbox).order_by(FeedbackOutbox.feedback_version))
        rows = result.scalars().all()

    assert len(rows) == 2
    assert [row.payload['redacted'] for row in rows] == [True, True]
    assert [row.payload['redaction_reason'] for row in rows] == ['feedback_deleted', 'feedback_deleted']
    assert all('feedback' not in row.payload for row in rows)
    assert all('previous_feedback' not in row.payload for row in rows)


@pytest.mark.asyncio
async def test_feedback_delete_redacts_existing_outbox_rows_when_bridge_disabled(db_sessionmaker):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    HERMES_BRIDGE_ENABLED=False,
                    HERMES_OUTBOX_DRAIN_ENABLED=False,
                    HERMES_DELETE_PURGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice', role='user')
    async with db_sessionmaker() as session:
        async with session.begin():
            created = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1, 'comment': 'private'}),
                db=session,
            )
            await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=created,
                actor_user_id='alice',
                actor_role='user',
            )

        assert await evaluations_router.delete_feedback_by_id(
            id=created.id,
            request=request,
            user=user,
            db=session,
        ) is True

        result = await session.execute(select(FeedbackOutbox).where(FeedbackOutbox.feedback_id == created.id))
        rows = result.scalars().all()

    assert len(rows) == 2
    assert [row.event_type for row in sorted(rows, key=lambda item: item.feedback_version)] == ['created', 'deleted']
    assert all(row.payload['redacted'] is True for row in rows)
    assert all(row.payload['redaction_reason'] == 'feedback_deleted' for row in rows)
    assert all('feedback' not in row.payload for row in rows)


@pytest.mark.asyncio
async def test_feedback_outbox_disabled_bypasses_enqueue(db_sessionmaker):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    HERMES_BRIDGE_ENABLED=False,
                    HERMES_OUTBOX_DRAIN_ENABLED=False,
                )
            )
        )
    )
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            await evaluations_router._enqueue_feedback_event_if_enabled(
                request,
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        assert await session.scalar(select(func.count()).select_from(FeedbackOutbox)) == 0


@pytest.mark.asyncio
async def test_feedback_outbox_capture_does_not_depend_on_drain_flag(db_sessionmaker):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                    config=SimpleNamespace(
                        HERMES_BRIDGE_ENABLED=True,
                        HERMES_OUTBOX_DRAIN_ENABLED=False,
                        HERMES_BRIDGE_URL='http://hermes',
                        HERMES_BRIDGE_API_KEY='secret',
                    )
                )
            )
        )
    assert feedback_outbox_capture_enabled(request.app.state.config) is True
    assert outbox_drain_enabled(request.app.state.config) is False

    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            await evaluations_router._enqueue_feedback_event_if_enabled(
                request,
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        assert await session.scalar(select(func.count()).select_from(FeedbackOutbox)) == 1


@pytest.mark.asyncio
async def test_feedback_outbox_reclaims_expired_in_progress_lease(db_sessionmaker, monkeypatch):
    import open_webui.internal.db as internal_db

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            event = await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        assert [row.event_id for row in await FeedbackOutboxes.claim_batch(now=100, lease_seconds=60, db=session)] == [
            event.event_id
        ]
        assert await FeedbackOutboxes.claim_batch(now=159, db=session) == []
        assert [row.event_id for row in await FeedbackOutboxes.claim_batch(now=160, db=session)] == [event.event_id]


@pytest.mark.asyncio
async def test_feedback_outbox_failed_mark_cannot_revert_sent_event(db_sessionmaker, monkeypatch):
    import open_webui.internal.db as internal_db

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            event = await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        assert [row.event_id for row in await FeedbackOutboxes.claim_batch(now=100, db=session)] == [event.event_id]
        assert await FeedbackOutboxes.mark_sent(event.event_id, db=session) is True
        assert await FeedbackOutboxes.mark_failed(event.event_id, 'late duplicate failure', now=101, db=session) is False

        result = await session.execute(select(FeedbackOutbox).where(FeedbackOutbox.event_id == event.event_id))
        row = result.scalars().one()
        assert row.status == 'sent'
        assert row.last_error is None
        assert row.payload['redacted'] is True
        assert 'feedback' not in row.payload


@pytest.mark.asyncio
async def test_feedback_outbox_stale_lease_cannot_mark_reclaimed_event_failed(db_sessionmaker, monkeypatch):
    import open_webui.internal.db as internal_db

    monkeypatch.setattr(internal_db, 'DATABASE_ENABLE_SESSION_SHARING', True)
    async with db_sessionmaker() as session:
        async with session.begin():
            feedback = await Feedbacks.insert_new_feedback_in_session(
                user_id='alice',
                form_data=FeedbackForm(type='rating', data={'rating': 1}),
                db=session,
            )
            event = await FeedbackOutboxes.enqueue_feedback_event(
                session,
                event_type='created',
                feedback=feedback,
                actor_user_id='alice',
                actor_role='user',
            )

        first_claim = (await FeedbackOutboxes.claim_batch(now=100, lease_seconds=60, db=session))[0]
        second_claim = (await FeedbackOutboxes.claim_batch(now=160, lease_seconds=60, db=session))[0]

        assert first_claim.lease_until == 160
        assert second_claim.lease_until == 220
        assert await FeedbackOutboxes.mark_failed(
            event.event_id,
            'stale worker failure',
            lease_until=first_claim.lease_until,
            now=161,
            db=session,
        ) is False
        assert await FeedbackOutboxes.mark_sent(
            event.event_id,
            lease_until=second_claim.lease_until,
            db=session,
        ) is True

        result = await session.execute(select(FeedbackOutbox).where(FeedbackOutbox.event_id == event.event_id))
        row = result.scalars().one()
        assert row.status == 'sent'
        assert row.last_error is None


@pytest.mark.asyncio
async def test_outbox_drain_disabled_does_not_claim_rows():
    class Config:
        HERMES_BRIDGE_ENABLED = False
        HERMES_OUTBOX_DRAIN_ENABLED = False

    assert await drain_feedback_outbox_once(Config) == {'claimed': 0, 'sent': 0, 'failed': 0, 'skipped': 1}


@pytest.mark.asyncio
async def test_delete_purge_mode_claims_only_delete_rows(monkeypatch):
    from open_webui.utils import hermes_outbox

    class Config:
        HERMES_BRIDGE_ENABLED = False
        HERMES_OUTBOX_DRAIN_ENABLED = False
        HERMES_DELETE_PURGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = 'secret'

    claim_args = {}

    class FakeOutboxes:
        async def claim_batch(self, **kwargs):
            claim_args.update(kwargs)
            return []

    monkeypatch.setattr(hermes_outbox, 'FeedbackOutboxes', FakeOutboxes())

    assert outbox_drain_enabled(Config) is False
    assert feedback_delete_purge_enabled(Config) is True
    assert outbox_worker_enabled(Config) is True
    assert await drain_feedback_outbox_once(Config) == {'claimed': 0, 'sent': 0, 'failed': 0, 'skipped': 0}
    assert claim_args['event_types'] == ['deleted']


@pytest.mark.asyncio
async def test_delete_purge_mode_posts_redacted_delete_payload(monkeypatch):
    from open_webui.utils import hermes_outbox

    class Config:
        HERMES_BRIDGE_ENABLED = False
        HERMES_OUTBOX_DRAIN_ENABLED = False
        HERMES_DELETE_PURGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = 'secret'

    delete_claim = SimpleNamespace(
        event_id='feedback:f1:2:deleted',
        feedback_id='f1',
        feedback_version=2,
        user_id='alice',
        event_type='deleted',
        lease_until=160,
        payload={
            'event_id': 'feedback:f1:2:deleted',
            'event_type': 'deleted',
            'feedback_id': 'f1',
            'user_id': 'alice',
            'actor_role': 'user',
            'version': 2,
            'redacted': True,
            'redaction_reason': 'feedback_deleted',
        },
    )
    posted = []
    marked = {}

    class FakeOutboxes:
        async def claim_batch(self, **kwargs):
            assert kwargs['event_types'] == ['deleted']
            return [delete_claim]

        async def get_claimed_event(self, event_id, *, lease_until=None):
            assert event_id == delete_claim.event_id
            assert lease_until == delete_claim.lease_until
            return delete_claim

        async def has_superseding_delete(self, feedback_id, feedback_version):
            raise AssertionError('delete events should be posted even when their payload is redacted')

        async def mark_sent(self, event_id, *, lease_until=None, redaction_reason='sent'):
            marked['event_id'] = event_id
            marked['lease_until'] = lease_until
            marked['redaction_reason'] = redaction_reason
            return True

        async def mark_failed(self, *args, **kwargs):
            raise AssertionError('delete purge post should not fail in this test')

    class FakeClient:
        def __init__(self, config):
            assert config.api_key == 'secret'

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post_feedback_event(self, payload, *, user_id, role):
            posted.append({'payload': payload, 'user_id': user_id, 'role': role})

    monkeypatch.setattr(hermes_outbox, 'FeedbackOutboxes', FakeOutboxes())
    monkeypatch.setattr(hermes_outbox, 'HermesBridgeClient', FakeClient)

    assert await drain_feedback_outbox_once(Config) == {'claimed': 1, 'sent': 1, 'failed': 0, 'skipped': 0}
    assert posted == [{'payload': delete_claim.payload, 'user_id': 'alice', 'role': 'user'}]
    assert marked == {
        'event_id': 'feedback:f1:2:deleted',
        'lease_until': 160,
        'redaction_reason': 'sent',
    }


@pytest.mark.asyncio
async def test_outbox_drain_rereads_claimed_row_and_skips_redacted_payload(monkeypatch):
    from open_webui.utils import hermes_outbox

    class Config:
        HERMES_BRIDGE_ENABLED = True
        HERMES_OUTBOX_DRAIN_ENABLED = True
        HERMES_DELETE_PURGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = 'secret'

    stale_claim = SimpleNamespace(
        event_id='feedback:f1:0:created',
        feedback_id='f1',
        feedback_version=0,
        user_id='alice',
        event_type='created',
        lease_until=160,
        payload={'event_id': 'feedback:f1:0:created', 'feedback': {'data': {'comment': 'private'}}},
    )
    current_claim = SimpleNamespace(
        **{
            **stale_claim.__dict__,
            'payload': {
                'event_id': 'feedback:f1:0:created',
                'event_type': 'created',
                'feedback_id': 'f1',
                'user_id': 'alice',
                'version': 0,
                'redacted': True,
                'redaction_reason': 'feedback_deleted',
            },
        }
    )
    marked = {}

    class FakeOutboxes:
        async def claim_batch(self, **kwargs):
            return [stale_claim]

        async def get_claimed_event(self, event_id, *, lease_until=None):
            assert event_id == stale_claim.event_id
            assert lease_until == stale_claim.lease_until
            return current_claim

        async def has_superseding_delete(self, feedback_id, feedback_version):
            raise AssertionError('redacted payload should skip before superseding-delete lookup')

        async def mark_sent(self, event_id, *, lease_until=None, redaction_reason='sent'):
            marked['event_id'] = event_id
            marked['lease_until'] = lease_until
            marked['redaction_reason'] = redaction_reason
            return True

        async def mark_failed(self, *args, **kwargs):
            raise AssertionError('redacted payload should not be posted or failed')

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post_feedback_event(self, *args, **kwargs):
            raise AssertionError('redacted payload should not be posted')

    monkeypatch.setattr(hermes_outbox, 'FeedbackOutboxes', FakeOutboxes())
    monkeypatch.setattr(hermes_outbox, 'HermesBridgeClient', FakeClient)

    assert await drain_feedback_outbox_once(Config) == {'claimed': 1, 'sent': 0, 'failed': 0, 'skipped': 1}
    assert marked == {
        'event_id': 'feedback:f1:0:created',
        'lease_until': 160,
        'redaction_reason': 'superseded_feedback_delete',
    }


def test_bridge_headers_include_user_role_bearer_and_idempotency():
    headers = build_hermes_headers(
        'alice',
        role='admin',
        chat_id='chat-1',
        idempotency_key='evt-1',
        config=HermesBridgeConfig(api_key='secret'),
    )
    assert headers['Authorization'] == 'Bearer secret'
    assert headers['X-OpenWebUI-User-Id'] == 'alice'
    assert headers['X-OpenWebUI-User-Role'] == 'admin'
    assert headers['X-OpenWebUI-Chat-Id'] == 'chat-1'
    assert headers['Idempotency-Key'] == 'evt-1'


def test_bridge_config_parses_string_boolean_overrides():
    class Config:
        HERMES_BRIDGE_ENABLED = 'false'
        HERMES_MEMORY_BRIDGE_ENABLED = 'yes'
        HERMES_OUTBOX_DRAIN_ENABLED = '0'
        HERMES_DELETE_PURGE_ENABLED = 'true'
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = 'secret'

    assert get_bridge_config(Config).enabled is False
    assert get_bridge_config(Config).memory_enabled is True
    assert get_bridge_config(Config).outbox_drain_enabled is False
    assert get_bridge_config(Config).delete_purge_enabled is True
    assert memory_bridge_enabled(Config) is False
    assert outbox_drain_enabled(Config) is False
    assert feedback_delete_purge_enabled(Config) is True


def test_bridge_delivery_features_require_internal_api_key(monkeypatch):
    monkeypatch.delenv('OPENWEBUI_HERMES_BRIDGE_API_KEY', raising=False)
    monkeypatch.delenv('HERMES_BRIDGE_API_KEY', raising=False)

    class Config:
        HERMES_BRIDGE_ENABLED = True
        HERMES_MEMORY_BRIDGE_ENABLED = True
        HERMES_OUTBOX_DRAIN_ENABLED = True
        HERMES_DELETE_PURGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = ''

    assert memory_bridge_enabled(Config) is False
    assert memory_bridge_misconfigured(Config) is True
    assert feedback_outbox_capture_enabled(Config) is True
    assert outbox_drain_enabled(Config) is False
    assert feedback_delete_purge_enabled(Config) is False
    assert outbox_worker_enabled(Config) is False


def test_memory_bridge_defaults_off_when_only_parent_bridge_is_enabled(monkeypatch):
    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_ENABLED', 'true')
    monkeypatch.delenv('OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED', raising=False)
    monkeypatch.delenv('HERMES_MEMORY_BRIDGE_ENABLED', raising=False)

    cfg = get_bridge_config()

    assert cfg.enabled is True
    assert cfg.memory_enabled is False
    assert memory_bridge_enabled() is False


def test_bridge_config_env_flags_override_persisted_app_config(monkeypatch):
    class Config:
        HERMES_BRIDGE_ENABLED = True
        HERMES_MEMORY_BRIDGE_ENABLED = True
        HERMES_OUTBOX_DRAIN_ENABLED = True
        HERMES_DELETE_PURGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://persisted'

    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_ENABLED', 'false')
    monkeypatch.setenv('OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED', '0')
    monkeypatch.setenv('OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED', 'no')
    monkeypatch.setenv('OPENWEBUI_HERMES_DELETE_PURGE_ENABLED', 'false')

    cfg = get_bridge_config(Config)

    assert cfg.enabled is False
    assert cfg.memory_enabled is False
    assert cfg.outbox_drain_enabled is False
    assert cfg.delete_purge_enabled is False
    assert memory_bridge_enabled(Config) is False
    assert outbox_drain_enabled(Config) is False


def test_bridge_config_env_flags_override_import_defaults_without_app_config(monkeypatch):
    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_ENABLED', 'false')
    monkeypatch.setenv('OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED', '0')
    monkeypatch.setenv('OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED', 'no')
    monkeypatch.setenv('OPENWEBUI_HERMES_DELETE_PURGE_ENABLED', 'false')

    cfg = get_bridge_config()

    assert cfg.enabled is False
    assert cfg.memory_enabled is False
    assert cfg.outbox_drain_enabled is False
    assert cfg.delete_purge_enabled is False


def test_bridge_config_legacy_env_flags_override_persisted_app_config(monkeypatch):
    class Config:
        HERMES_BRIDGE_ENABLED = True
        HERMES_MEMORY_BRIDGE_ENABLED = True
        HERMES_OUTBOX_DRAIN_ENABLED = True
        HERMES_DELETE_PURGE_ENABLED = True

    monkeypatch.setenv('HERMES_BRIDGE_ENABLED', '0')
    monkeypatch.setenv('HERMES_MEMORY_BRIDGE_ENABLED', 'false')
    monkeypatch.setenv('HERMES_OUTBOX_DRAIN_ENABLED', 'off')
    monkeypatch.setenv('HERMES_DELETE_PURGE_ENABLED', 'off')

    cfg = get_bridge_config(Config)

    assert cfg.enabled is False
    assert cfg.memory_enabled is False
    assert cfg.outbox_drain_enabled is False
    assert cfg.delete_purge_enabled is False


def test_bridge_config_explicit_env_values_override_persisted_app_config(monkeypatch):
    class Config:
        HERMES_BRIDGE_ENABLED = False
        HERMES_MEMORY_BRIDGE_ENABLED = False
        HERMES_OUTBOX_DRAIN_ENABLED = False
        HERMES_DELETE_PURGE_ENABLED = False
        HERMES_BRIDGE_URL = 'http://persisted'
        HERMES_BRIDGE_API_KEY = 'persisted-key'
        HERMES_BRIDGE_TIMEOUT = 99

    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_ENABLED', 'true')
    monkeypatch.setenv('OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED', 'yes')
    monkeypatch.setenv('OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED', '1')
    monkeypatch.setenv('OPENWEBUI_HERMES_DELETE_PURGE_ENABLED', 'true')
    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_URL', 'http://env-hermes')
    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_API_KEY', 'env-key')
    monkeypatch.setenv('OPENWEBUI_HERMES_BRIDGE_TIMEOUT', '3.5')

    cfg = get_bridge_config(Config)

    assert cfg.enabled is True
    assert cfg.memory_enabled is True
    assert cfg.outbox_drain_enabled is True
    assert cfg.delete_purge_enabled is True
    assert cfg.url == 'http://env-hermes'
    assert cfg.api_key == 'env-key'
    assert cfg.timeout == 3.5


class _FakeResponse:
    def __init__(self, data, *, status_code=200):
        self._data = data
        self.status_code = status_code
        self.request = httpx.Request('GET', 'http://hermes.test')

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f'HTTP {self.status_code}',
                request=self.request,
                response=self,
            )
        return None

    def json(self):
        return self._data


class _FakeHTTPClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [_FakeResponse({'status': 'created'})])

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_feedback_bridge_client_posts_idempotent_event():
    fake = _FakeHTTPClient()
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)
    await client.post_feedback_event({'event_id': 'evt-1', 'feedback_id': 'f1'}, user_id='alice')

    method, path, kwargs = fake.calls[0]
    assert method == 'POST'
    assert path == '/api/openwebui/feedback-events'
    assert kwargs['headers']['Authorization'] == 'Bearer k'
    assert kwargs['headers']['X-OpenWebUI-User-Id'] == 'alice'
    assert kwargs['headers']['Idempotency-Key'] == 'evt-1'
    assert kwargs['json']['event_id'] == 'evt-1'


@pytest.mark.asyncio
async def test_bridge_update_memory_requires_existing_and_returns_none_on_404():
    fake = _FakeHTTPClient([_FakeResponse({'error': 'not found'}, status_code=404)])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    result = await client.update_memory(user_id='alice', memory_id='missing', content='new text')

    method, path, kwargs = fake.calls[0]
    assert result is None
    assert method == 'POST'
    assert path == '/api/openwebui/memories'
    assert kwargs['json'] == {
        'id': 'missing',
        'content': 'new text',
        'source': 'openwebui-ui',
        'require_existing': True,
    }


@pytest.mark.asyncio
async def test_bridge_update_memory_raises_conflict_on_retracted_memory():
    fake = _FakeHTTPClient([_FakeResponse({'error': 'memory is retracted'}, status_code=409)])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    with pytest.raises(ValueError):
        await client.update_memory(user_id='alice', memory_id='m1', content='new text')


@pytest.mark.asyncio
async def test_bridge_delete_memory_returns_hermes_deleted_flag():
    fake = _FakeHTTPClient([_FakeResponse({'status': 'not_found', 'deleted': False})])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    assert await client.delete_memory(user_id='alice', memory_id='missing') is False

    fake = _FakeHTTPClient([_FakeResponse({'status': 'deleted', 'deleted': True})])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    assert await client.delete_memory(user_id='alice', memory_id='m1') is True


@pytest.mark.asyncio
async def test_bridge_list_memories_page_marks_unknown_non_empty_list_as_maybe_capped():
    fake = _FakeHTTPClient([_FakeResponse({'memories': [{'id': 'm1'}]})])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    page = await client.list_memories_page(user_id='alice')

    assert page.memories == [{'id': 'm1'}]
    assert page.may_be_capped is True


@pytest.mark.asyncio
async def test_bridge_list_memories_page_accepts_complete_total_metadata():
    fake = _FakeHTTPClient([_FakeResponse({'memories': [{'id': 'm1'}], 'total': 1})])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    page = await client.list_memories_page(user_id='alice')

    assert page.memories == [{'id': 'm1'}]
    assert page.may_be_capped is False


@pytest.mark.asyncio
async def test_bridge_list_memories_page_marks_has_more_as_capped():
    fake = _FakeHTTPClient([_FakeResponse({'memories': [{'id': 'm1'}], 'has_more': True})])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    page = await client.list_memories_page(user_id='alice')

    assert page.may_be_capped is True


@pytest.mark.asyncio
async def test_bridge_list_memories_page_marks_empty_truncated_response_as_capped():
    fake = _FakeHTTPClient([
        _FakeResponse({'memories': [], 'total': 0, 'has_more': True, 'truncated': True})
    ])
    client = HermesBridgeClient(HermesBridgeConfig(url='http://hermes', api_key='k'), http_client=fake)

    page = await client.list_memories_page(user_id='alice')

    assert page.memories == []
    assert page.may_be_capped is True


def test_memory_search_result_trusts_semantic_bridge_hits_and_scores():
    result = build_memory_search_result(
        [{'id': 'm1', 'content': 'likes sushi for lunch', 'created_at': 10, 'type': 'preferences', 'score': 0.83}],
        query='food preferences',
    )
    assert result['ids'] == [['m1']]
    assert result['documents'] == [['likes sushi for lunch']]
    assert result['distances'][0][0] == 0.83


def test_memory_search_result_accepts_memory_id_shape():
    result = build_memory_search_result(
        [{'memory_id': 'm1', 'content': 'likes tea', 'created_at': 10, 'type': 'preferences'}],
        query='drink preferences',
    )

    assert result['ids'] == [['m1']]


def test_memory_search_result_skips_malformed_hits_without_id():
    result = build_memory_search_result(
        [
            {'content': 'missing id'},
            {'id': 'm1', 'content': 'likes tea', 'created_at': 10, 'type': 'preferences'},
        ],
        query='drink preferences',
    )

    assert result['ids'] == [['m1']]
    assert result['documents'] == [['likes tea']]


def test_memory_model_from_bridge_rejects_malformed_payload_without_id():
    with pytest.raises(HermesMalformedMemoryResponseError):
        memory_model_from_bridge({'content': 'missing id'})


@pytest.mark.asyncio
async def test_chat_memory_handler_fails_closed_when_bridge_is_misconfigured():
    from open_webui.utils import middleware as middleware_utils

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='',
                )
            )
        )
    )
    form_data = {'messages': [{'role': 'user', 'content': 'remember this'}]}
    user = SimpleNamespace(id='alice')

    with pytest.raises(HTTPException) as exc:
        await middleware_utils.chat_memory_handler(request, form_data, {}, user)

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_bridge_bulk_memory_delete_reports_partial_failure(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')
    deleted = []

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_memories(self, *, user_id):
            assert user_id == 'alice'
            return [{'id': 'ok'}, {'memory_id': 'missing'}]

        async def list_memories_page(self, *, user_id):
            assert user_id == 'alice'
            return HermesMemoryListPage(
                memories=[{'id': 'ok'}, {'memory_id': 'missing'}],
                may_be_capped=False,
            )

        async def delete_memory(self, *, user_id, memory_id):
            deleted.append(memory_id)
            return memory_id == 'ok'

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)

    assert await memories_router.delete_memory_by_user_id(request=request, user=user, db=None) is False
    assert deleted == ['ok', 'missing']


@pytest.mark.asyncio
async def test_bridge_bulk_memory_delete_treats_empty_list_as_successful_noop(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')
    local_cleanup = []

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_memories(self, *, user_id):
            assert user_id == 'alice'
            return []

        async def list_memories_page(self, *, user_id):
            assert user_id == 'alice'
            return HermesMemoryListPage(memories=[], may_be_capped=False)

    async def fake_local_cleanup(user_id, db=None):
        local_cleanup.append((user_id, db))

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)
    monkeypatch.setattr(memories_router, '_best_effort_delete_local_memory_collection', fake_local_cleanup)

    assert await memories_router.delete_memory_by_user_id(request=request, user=user, db=None) is True
    assert local_cleanup == [('alice', None)]


@pytest.mark.asyncio
async def test_bridge_bulk_memory_delete_fails_closed_when_list_may_be_capped(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')
    deleted = []
    local_cleanup = []

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_memories_page(self, *, user_id):
            assert user_id == 'alice'
            return HermesMemoryListPage(memories=[{'id': 'm1'}], may_be_capped=True)

        async def delete_memory(self, *, user_id, memory_id):
            deleted.append(memory_id)
            return True

    async def fake_local_cleanup(user_id, db=None):
        local_cleanup.append((user_id, db))

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)
    monkeypatch.setattr(memories_router, '_best_effort_delete_local_memory_collection', fake_local_cleanup)

    assert await memories_router.delete_memory_by_user_id(request=request, user=user, db=None) is False
    assert deleted == []
    assert local_cleanup == []


@pytest.mark.asyncio
async def test_bridge_bulk_memory_delete_fails_closed_for_empty_truncated_list(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')
    local_cleanup = []

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_memories_page(self, *, user_id):
            assert user_id == 'alice'
            return HermesMemoryListPage(memories=[], may_be_capped=True)

        async def delete_memory(self, *, user_id, memory_id):
            raise AssertionError('delete_memory must not run for a capped list')

    async def fake_local_cleanup(user_id, db=None):
        local_cleanup.append((user_id, db))

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)
    monkeypatch.setattr(memories_router, '_best_effort_delete_local_memory_collection', fake_local_cleanup)

    assert await memories_router.delete_memory_by_user_id(request=request, user=user, db=None) is False
    assert local_cleanup == []


@pytest.mark.asyncio
async def test_bridge_delete_memory_by_id_preserves_false_not_found_semantics(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def delete_memory(self, *, user_id, memory_id):
            assert user_id == 'alice'
            assert memory_id == 'missing'
            return False

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)

    assert await memories_router.delete_memory_by_id(memory_id='missing', request=request, user=user, db=None) is False


@pytest.mark.asyncio
async def test_bridge_delete_memory_by_id_cleans_local_memory_after_hermes_success(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')
    local_cleanup = []

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def delete_memory(self, *, user_id, memory_id):
            assert user_id == 'alice'
            assert memory_id == 'm1'
            return True

    async def fake_local_cleanup(user_id, memory_id, db=None):
        local_cleanup.append((user_id, memory_id, db))

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)
    monkeypatch.setattr(memories_router, '_best_effort_delete_local_memory', fake_local_cleanup)

    assert await memories_router.delete_memory_by_id(memory_id='m1', request=request, user=user, db='db') is True
    assert local_cleanup == [('alice', 'm1', 'db')]


@pytest.mark.asyncio
async def test_bridge_delete_memory_by_id_ignores_local_cleanup_failure(monkeypatch, caplog):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def delete_memory(self, *, user_id, memory_id):
            assert user_id == 'alice'
            assert memory_id == 'm1'
            return True

    async def fake_delete_memory_by_id_and_user_id(memory_id, user_id, db=None):
        assert (memory_id, user_id, db) == ('m1', 'alice', None)
        raise RuntimeError('db unavailable')

    async def fake_vector_delete(*, collection_name, ids):
        assert collection_name == 'user-memory-alice'
        assert ids == ['m1']
        raise RuntimeError('vector unavailable')

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)
    monkeypatch.setattr(
        memories_router.Memories,
        'delete_memory_by_id_and_user_id',
        fake_delete_memory_by_id_and_user_id,
    )
    monkeypatch.setattr(memories_router.ASYNC_VECTOR_DB_CLIENT, 'delete', fake_vector_delete)

    assert await memories_router.delete_memory_by_id(memory_id='m1', request=request, user=user, db=None) is True
    assert 'Failed to remove local OpenWebUI memory row after Hermes delete' in caplog.text
    assert 'Failed to remove local OpenWebUI memory vector after Hermes delete' in caplog.text


@pytest.mark.asyncio
async def test_get_memories_uses_bridge_and_skips_malformed_rows(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def list_memories(self, *, user_id):
            assert user_id == 'alice'
            return [
                {'content': 'bad row without id'},
                {'memory_id': 'm1', 'content': 'likes tea', 'created_at': 10, 'updated_at': 11},
            ]

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)

    result = await memories_router.get_memories(request=request, user=user, db=None)

    assert [memory.id for memory in result] == ['m1']
    assert result[0].user_id == 'alice'
    assert result[0].content == 'likes tea'


@pytest.mark.asyncio
async def test_add_memory_bridge_malformed_create_response_returns_structured_502(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def create_memory(self, *, user_id, content):
            assert user_id == 'alice'
            assert content == 'likes tea'
            return {'content': 'likes tea'}

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)

    with pytest.raises(HTTPException) as exc_info:
        await memories_router.add_memory(
            request=request,
            form_data=memories_router.AddMemoryForm(content='likes tea'),
            user=user,
        )

    assert exc_info.value.status_code == 502
    assert 'id or memory_id' in exc_info.value.detail


@pytest.mark.asyncio
async def test_update_memory_bridge_malformed_update_response_returns_structured_502(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='secret',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        assert permission == 'features.memories'
        assert permissions == {}
        return True

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def update_memory(self, *, user_id, memory_id, content):
            assert user_id == 'alice'
            assert memory_id == 'm1'
            assert content == 'likes coffee'
            return {'content': 'likes coffee'}

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)
    monkeypatch.setattr(memories_router, 'HermesBridgeClient', FakeClient)

    with pytest.raises(HTTPException) as exc_info:
        await memories_router.update_memory_by_id(
            memory_id='m1',
            request=request,
            form_data=memories_router.MemoryUpdateModel(content='likes coffee'),
            user=user,
        )

    assert exc_info.value.status_code == 502
    assert 'id or memory_id' in exc_info.value.detail


@pytest.mark.asyncio
async def test_memory_bridge_routes_fail_closed_when_enabled_without_api_key(monkeypatch):
    monkeypatch.delenv('OPENWEBUI_HERMES_BRIDGE_API_KEY', raising=False)
    monkeypatch.delenv('HERMES_BRIDGE_API_KEY', raising=False)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_MEMORIES=True,
                    USER_PERMISSIONS={},
                    HERMES_BRIDGE_ENABLED=True,
                    HERMES_MEMORY_BRIDGE_ENABLED=True,
                    HERMES_BRIDGE_URL='http://hermes',
                    HERMES_BRIDGE_API_KEY='',
                )
            )
        )
    )
    user = SimpleNamespace(id='alice')

    async def fake_has_permission(user_id, permission, permissions):
        assert user_id == 'alice'
        return True

    monkeypatch.setattr(memories_router, 'has_permission', fake_has_permission)

    with pytest.raises(HTTPException) as exc_info:
        await memories_router.get_memories(request=request, user=user, db=None)

    assert exc_info.value.status_code == 503
    assert 'HERMES_BRIDGE_API_KEY' in exc_info.value.detail


@pytest.mark.asyncio
async def test_builtin_search_memories_accepts_bridge_dict_results(monkeypatch):
    from open_webui.tools import builtin

    async def fake_query_memory(request, form, user):
        assert form.content == 'food'
        assert form.k == 3
        assert user.id == 'alice'
        return {
            'ids': [['m1']],
            'documents': [['likes sushi for lunch']],
            'metadatas': [[{'created_at': 1_700_000_000}]],
            'distances': [[0.83]],
        }

    monkeypatch.setattr(builtin, 'query_memory', fake_query_memory)

    result = await builtin.search_memories(
        'food',
        count=3,
        __request__=SimpleNamespace(),
        __user__={
            'id': 'alice',
            'email': 'alice@example.com',
            'name': 'Alice',
            'role': 'user',
            'last_active_at': 1,
            'updated_at': 1,
            'created_at': 1,
        },
    )

    assert json.loads(result) == [
        {
            'id': 'm1',
            'date': '2023-11-14',
            'content': 'likes sushi for lunch',
        }
    ]


def test_hermes_prompt_injection_guard():
    class Config:
        HERMES_BRIDGE_ENABLED = True
        HERMES_MEMORY_BRIDGE_ENABLED = True
        HERMES_BRIDGE_URL = 'http://hermes'
        HERMES_BRIDGE_API_KEY = 'secret'

    assert should_bypass_openwebui_memory_injection(Config, 'hermes-agent') is True
    assert should_bypass_openwebui_memory_injection(Config, 'other-model') is False


def test_memory_search_result_keeps_substring_fallback_score():
    result = build_memory_search_result(
        [{'id': 'm1', 'content': 'likes deterministic tests', 'created_at': 10, 'type': 'preferences'}],
        query='deterministic',
    )
    assert result['ids'] == [['m1']]
    assert result['distances'][0][0] == 0.95

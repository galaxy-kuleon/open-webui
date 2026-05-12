import time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, JSON, Text, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.feedbacks import FeedbackModel


class FeedbackOutbox(Base):
    __tablename__ = 'feedback_outbox'

    id = Column(Text, primary_key=True)
    event_id = Column(Text, unique=True, index=True, nullable=False)
    feedback_id = Column(Text, index=True, nullable=False)
    feedback_version = Column(BigInteger, nullable=False)
    user_id = Column(Text, index=True, nullable=False)
    event_type = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(Text, index=True, nullable=False, default='pending')
    attempts = Column(BigInteger, nullable=False, default=0)
    next_attempt_at = Column(BigInteger, nullable=False, default=0)
    lease_until = Column(BigInteger, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class FeedbackOutboxModel(BaseModel):
    id: str
    event_id: str
    feedback_id: str
    feedback_version: int
    user_id: str
    event_type: str
    payload: dict[str, Any]
    status: str
    attempts: int = 0
    next_attempt_at: int = 0
    lease_until: int = 0
    last_error: Optional[str] = None
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class FeedbackOutboxStatus(BaseModel):
    pending: int = 0
    in_progress: int = 0
    sent: int = 0
    failed: int = 0
    total: int = 0


class FeedbackOutboxTable:
    @staticmethod
    def build_event_id(feedback_id: str, version: int, event_type: str) -> str:
        return f'feedback:{feedback_id}:{version}:{event_type}'

    @staticmethod
    def build_payload(
        *,
        event_type: str,
        feedback: FeedbackModel,
        actor_user_id: str,
        actor_role: str,
        previous: Optional[FeedbackModel] = None,
    ) -> dict[str, Any]:
        payload = {
            'event_id': FeedbackOutboxTable.build_event_id(feedback.id, feedback.version, event_type),
            'event_type': event_type,
            'feedback_id': feedback.id,
            'user_id': feedback.user_id,
            'actor_user_id': actor_user_id,
            'actor_role': actor_role,
            'version': feedback.version,
            'created_at': feedback.created_at,
            'updated_at': feedback.updated_at,
        }
        if event_type != 'deleted':
            payload['feedback'] = feedback.model_dump()
        if previous is not None:
            payload['previous_feedback'] = previous.model_dump()
        return payload

    @staticmethod
    def redacted_payload(payload: dict[str, Any], *, reason: str) -> dict[str, Any]:
        keep_keys = {
            'event_id',
            'event_type',
            'feedback_id',
            'user_id',
            'actor_user_id',
            'actor_role',
            'version',
            'created_at',
            'updated_at',
        }
        redacted = {key: payload[key] for key in keep_keys if key in payload}
        redacted['redacted'] = True
        redacted['redaction_reason'] = reason
        redacted['redacted_at'] = int(time.time())
        return redacted

    async def enqueue_feedback_event(
        self,
        db: AsyncSession,
        *,
        event_type: str,
        feedback: FeedbackModel,
        actor_user_id: str,
        actor_role: str,
        previous: Optional[FeedbackModel] = None,
    ) -> FeedbackOutboxModel:
        now = int(time.time())
        payload = self.build_payload(
            event_type=event_type,
            feedback=feedback,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            previous=previous,
        )
        row = FeedbackOutbox(
            id=payload['event_id'],
            event_id=payload['event_id'],
            feedback_id=feedback.id,
            feedback_version=feedback.version,
            user_id=feedback.user_id,
            event_type=event_type,
            payload=payload,
            status='pending',
            attempts=0,
            next_attempt_at=0,
            lease_until=0,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.flush()
        return FeedbackOutboxModel.model_validate(row)

    async def claim_batch(
        self,
        *,
        limit: int = 50,
        lease_seconds: int = 60,
        now: Optional[int] = None,
        event_types: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[FeedbackOutboxModel]:
        now = int(now or time.time())
        async with get_async_db_context(db) as session:
            predicates = [
                FeedbackOutbox.status.in_(['pending', 'failed', 'in_progress']),
                FeedbackOutbox.next_attempt_at <= now,
                FeedbackOutbox.lease_until <= now,
            ]
            if event_types:
                predicates.append(FeedbackOutbox.event_type.in_(event_types))
            candidate_result = await session.execute(
                select(FeedbackOutbox)
                .where(*predicates)
                .order_by(
                    FeedbackOutbox.created_at.asc(),
                    FeedbackOutbox.feedback_id.asc(),
                    FeedbackOutbox.feedback_version.asc(),
                    FeedbackOutbox.event_id.asc(),
                )
                .limit(limit)
            )
            candidate_ids = [row.event_id for row in candidate_result.scalars().all()]
            claimed_ids: list[str] = []
            for event_id in candidate_ids:
                claim_predicates = [
                    FeedbackOutbox.event_id == event_id,
                    FeedbackOutbox.status.in_(['pending', 'failed', 'in_progress']),
                    FeedbackOutbox.next_attempt_at <= now,
                    FeedbackOutbox.lease_until <= now,
                ]
                if event_types:
                    claim_predicates.append(FeedbackOutbox.event_type.in_(event_types))
                result = await session.execute(
                    update(FeedbackOutbox)
                    .where(*claim_predicates)
                    .values(status='in_progress', lease_until=now + lease_seconds, updated_at=now)
                )
                if result.rowcount:
                    claimed_ids.append(event_id)
            await session.commit()
            if not claimed_ids:
                return []
            result = await session.execute(
                select(FeedbackOutbox)
                .where(FeedbackOutbox.event_id.in_(claimed_ids))
                .order_by(
                    FeedbackOutbox.created_at.asc(),
                    FeedbackOutbox.feedback_id.asc(),
                    FeedbackOutbox.feedback_version.asc(),
                    FeedbackOutbox.event_id.asc(),
                )
            )
            return [FeedbackOutboxModel.model_validate(row) for row in result.scalars().all()]

    async def get_claimed_event(
        self,
        event_id: str,
        *,
        lease_until: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[FeedbackOutboxModel]:
        predicates = [FeedbackOutbox.event_id == event_id, FeedbackOutbox.status == 'in_progress']
        if lease_until is not None:
            predicates.append(FeedbackOutbox.lease_until == lease_until)
        if db is not None:
            result = await db.execute(select(FeedbackOutbox).where(*predicates))
            row = result.scalars().first()
            return FeedbackOutboxModel.model_validate(row) if row else None
        async with get_async_db_context(None) as session:
            result = await session.execute(select(FeedbackOutbox).where(*predicates))
            row = result.scalars().first()
            return FeedbackOutboxModel.model_validate(row) if row else None

    async def has_feedback_history(
        self,
        feedback_id: str,
        *,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        if db is not None:
            result = await db.execute(
                select(FeedbackOutbox.event_id).where(FeedbackOutbox.feedback_id == feedback_id).limit(1)
            )
            return result.scalar_one_or_none() is not None
        async with get_async_db_context(None) as session:
            result = await session.execute(
                select(FeedbackOutbox.event_id).where(FeedbackOutbox.feedback_id == feedback_id).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def has_superseding_delete(
        self,
        feedback_id: str,
        feedback_version: int,
        *,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        stmt = (
            select(FeedbackOutbox.event_id)
            .where(
                FeedbackOutbox.feedback_id == feedback_id,
                FeedbackOutbox.event_type == 'deleted',
                FeedbackOutbox.feedback_version >= feedback_version,
            )
            .limit(1)
        )
        if db is not None:
            result = await db.execute(stmt)
            return result.scalar_one_or_none() is not None
        async with get_async_db_context(None) as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    async def mark_sent(
        self,
        event_id: str,
        *,
        lease_until: Optional[int] = None,
        redaction_reason: str = 'sent',
        db: Optional[AsyncSession] = None,
    ) -> bool:
        now = int(time.time())
        async with get_async_db_context(db) as session:
            row_result = await session.execute(
                select(FeedbackOutbox.payload).where(FeedbackOutbox.event_id == event_id)
            )
            payload = row_result.scalar_one_or_none()
            predicate = [FeedbackOutbox.event_id == event_id, FeedbackOutbox.status == 'in_progress']
            if lease_until is not None:
                predicate.append(FeedbackOutbox.lease_until == lease_until)
            values = {
                'status': 'sent',
                'lease_until': 0,
                'last_error': None,
                'updated_at': now,
            }
            if isinstance(payload, dict):
                values['payload'] = self.redacted_payload(payload, reason=redaction_reason)
            result = await session.execute(
                update(FeedbackOutbox)
                .where(*predicate)
                .values(**values)
            )
            await session.commit()
            return result.rowcount > 0

    async def mark_failed(
        self,
        event_id: str,
        error: str,
        *,
        lease_until: Optional[int] = None,
        now: Optional[int] = None,
        db: Optional[AsyncSession] = None,
    ) -> bool:
        now = int(now or time.time())
        async with get_async_db_context(db) as session:
            row_result = await session.execute(select(FeedbackOutbox.attempts).filter_by(event_id=event_id))
            current_attempts = row_result.scalar_one_or_none()
            if current_attempts is None:
                return False
            attempts = int(current_attempts or 0) + 1
            # Exponential backoff capped at 15 minutes; status remains retryable.
            delay = min(900, 2 ** min(attempts, 10))
            predicate = [FeedbackOutbox.event_id == event_id, FeedbackOutbox.status == 'in_progress']
            if lease_until is not None:
                predicate.append(FeedbackOutbox.lease_until == lease_until)
            result = await session.execute(
                update(FeedbackOutbox)
                .where(*predicate)
                .values(
                    status='failed',
                    attempts=attempts,
                    next_attempt_at=now + delay,
                    lease_until=0,
                    last_error=error[:1000],
                    updated_at=now,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def status_counts(self, *, db: Optional[AsyncSession] = None) -> FeedbackOutboxStatus:
        async with get_async_db_context(db) as session:
            result = await session.execute(select(FeedbackOutbox.status, func.count()).group_by(FeedbackOutbox.status))
            counts = {status: int(count) for status, count in result.all()}
            total = sum(counts.values())
            return FeedbackOutboxStatus(
                pending=counts.get('pending', 0),
                in_progress=counts.get('in_progress', 0),
                sent=counts.get('sent', 0),
                failed=counts.get('failed', 0),
                total=total,
            )

    async def redact_feedback_payloads(
        self,
        db: AsyncSession,
        *,
        feedback_id: str,
        reason: str,
    ) -> int:
        result = await db.execute(select(FeedbackOutbox).where(FeedbackOutbox.feedback_id == feedback_id))
        rows = list(result.scalars().all())
        now = int(time.time())
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            row.payload = self.redacted_payload(payload, reason=reason)
            row.updated_at = now
        await db.flush()
        return len(rows)


FeedbackOutboxes = FeedbackOutboxTable()

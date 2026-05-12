import logging
import time
import uuid
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from open_webui.internal.db import Base, JSONField, get_async_db_context
from open_webui.models.users import User, UserModel

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, JSON, Boolean

log = logging.getLogger(__name__)


####################
# Feedback DB Schema
####################


class Feedback(Base):
    __tablename__ = 'feedback'
    id = Column(Text, primary_key=True, unique=True)
    user_id = Column(Text)
    version = Column(BigInteger, default=0)
    type = Column(Text)
    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    snapshot = Column(JSON, nullable=True)
    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class FeedbackModel(BaseModel):
    id: str
    user_id: str
    version: int
    type: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    snapshot: Optional[dict] = None
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


####################
# Forms
####################


class FeedbackResponse(BaseModel):
    id: str
    user_id: str
    version: int
    type: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    created_at: int
    updated_at: int


class FeedbackIdResponse(BaseModel):
    id: str
    user_id: str
    created_at: int
    updated_at: int


class LeaderboardFeedbackData(BaseModel):
    """Minimal feedback data for leaderboard computation (excludes snapshot/meta)."""

    id: str
    data: Optional[dict] = None


class RatingData(BaseModel):
    rating: Optional[str | int] = None
    model_id: Optional[str] = None
    sibling_model_ids: Optional[list[str]] = None
    reason: Optional[str] = None
    comment: Optional[str] = None
    model_config = ConfigDict(extra='allow', protected_namespaces=())


class MetaData(BaseModel):
    arena: Optional[bool] = None
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    tags: Optional[list[str]] = None
    model_config = ConfigDict(extra='allow')


class SnapshotData(BaseModel):
    chat: Optional[dict] = None
    model_config = ConfigDict(extra='allow')


class FeedbackForm(BaseModel):
    type: str
    data: Optional[RatingData] = None
    meta: Optional[dict] = None
    snapshot: Optional[SnapshotData] = None
    # ignore: drop client-supplied id/user_id/version/timestamps at parse time.
    model_config = ConfigDict(extra='ignore')


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str = 'pending'

    last_active_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch
    created_at: int  # timestamp in epoch

    model_config = ConfigDict(from_attributes=True)


class FeedbackUserResponse(FeedbackResponse):
    user: Optional[UserResponse] = None


class FeedbackListResponse(BaseModel):
    items: list[FeedbackUserResponse]
    total: int


class ModelHistoryEntry(BaseModel):
    date: str
    won: int
    lost: int


class ModelHistoryResponse(BaseModel):
    model_id: str
    history: list[ModelHistoryEntry]


class FeedbackTable:
    @staticmethod
    def _form_update_values(form_data: FeedbackForm) -> dict:
        values = {}
        if form_data.data:
            values['data'] = form_data.data.model_dump()
        if form_data.meta:
            values['meta'] = form_data.meta
        if form_data.snapshot:
            values['snapshot'] = form_data.snapshot.model_dump()
        return values

    async def insert_new_feedback_in_session(
        self, user_id: str, form_data: FeedbackForm, db: AsyncSession
    ) -> FeedbackModel:
        id = str(uuid.uuid4())
        now = int(time.time())
        feedback = FeedbackModel(
            **{
                'id': id,
                'user_id': user_id,
                'version': 0,
                **form_data.model_dump(),
                'created_at': now,
                'updated_at': now,
            }
        )
        result = Feedback(**feedback.model_dump())
        db.add(result)
        await db.flush()
        await db.refresh(result)
        return FeedbackModel.model_validate(result)

    async def insert_new_feedback(
        self, user_id: str, form_data: FeedbackForm, db: Optional[AsyncSession] = None
    ) -> Optional[FeedbackModel]:
        async with get_async_db_context(db) as db:
            try:
                feedback = await self.insert_new_feedback_in_session(user_id, form_data, db)
                await db.commit()
                return feedback
            except Exception as e:
                log.exception(f'Error creating a new feedback: {e}')
                return None

    async def get_feedback_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[FeedbackModel]:
        try:
            async with get_async_db_context(db) as db:
                return await self.get_feedback_by_id_in_session(id=id, db=db)
        except Exception:
            return None

    async def get_feedback_by_id_in_session(
        self,
        id: str,
        db: AsyncSession,
        *,
        for_update: bool = False,
    ) -> Optional[FeedbackModel]:
        stmt = select(Feedback).filter_by(id=id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        feedback = result.scalars().first()
        if not feedback:
            return None
        return FeedbackModel.model_validate(feedback)

    async def get_feedback_by_id_and_user_id(
        self, id: str, user_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[FeedbackModel]:
        try:
            async with get_async_db_context(db) as db:
                return await self.get_feedback_by_id_and_user_id_in_session(id=id, user_id=user_id, db=db)
        except Exception:
            return None

    async def get_feedback_by_id_and_user_id_in_session(
        self,
        id: str,
        user_id: str,
        db: AsyncSession,
        *,
        for_update: bool = False,
    ) -> Optional[FeedbackModel]:
        stmt = select(Feedback).filter_by(id=id, user_id=user_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await db.execute(stmt)
        feedback = result.scalars().first()
        if not feedback:
            return None
        return FeedbackModel.model_validate(feedback)

    async def get_feedbacks_by_chat_id(self, chat_id: str, db: Optional[AsyncSession] = None) -> list[FeedbackModel]:
        """Get all feedbacks for a specific chat."""
        try:
            async with get_async_db_context(db) as db:
                # meta.chat_id stores the chat reference
                result = await db.execute(
                    select(Feedback)
                    .filter(Feedback.meta['chat_id'].as_string() == chat_id)
                    .order_by(Feedback.created_at.desc())
                )
                feedbacks = result.scalars().all()
                return [FeedbackModel.model_validate(fb) for fb in feedbacks]
        except Exception:
            return []

    async def get_feedback_items(
        self,
        filter: dict = {},
        skip: int = 0,
        limit: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> FeedbackListResponse:
        async with get_async_db_context(db) as db:
            stmt = select(Feedback, User).join(User, Feedback.user_id == User.id)

            if filter:
                # Apply model_id filter (exact match)
                model_id = filter.get('model_id')
                if model_id:
                    stmt = stmt.filter(Feedback.data['model_id'].as_string() == model_id)

                order_by = filter.get('order_by')
                direction = filter.get('direction')

                if order_by == 'username':
                    if direction == 'asc':
                        stmt = stmt.order_by(User.name.asc())
                    else:
                        stmt = stmt.order_by(User.name.desc())
                elif order_by == 'model_id':
                    if direction == 'asc':
                        stmt = stmt.order_by(Feedback.data['model_id'].as_string().asc())
                    else:
                        stmt = stmt.order_by(Feedback.data['model_id'].as_string().desc())
                elif order_by == 'rating':
                    if direction == 'asc':
                        stmt = stmt.order_by(Feedback.data['rating'].as_string().asc())
                    else:
                        stmt = stmt.order_by(Feedback.data['rating'].as_string().desc())
                elif order_by == 'updated_at':
                    if direction == 'asc':
                        stmt = stmt.order_by(Feedback.updated_at.asc())
                    else:
                        stmt = stmt.order_by(Feedback.updated_at.desc())

            else:
                stmt = stmt.order_by(Feedback.created_at.desc())

            # Count BEFORE pagination
            count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
            total = count_result.scalar()

            if skip:
                stmt = stmt.offset(skip)
            if limit:
                stmt = stmt.limit(limit)

            result = await db.execute(stmt)
            items = result.all()

            feedbacks = []
            for feedback, user in items:
                feedback_model = FeedbackModel.model_validate(feedback)
                user_model = UserResponse.model_validate(user)
                feedbacks.append(FeedbackUserResponse(**feedback_model.model_dump(), user=user_model))

            return FeedbackListResponse(items=feedbacks, total=total)

    async def get_all_feedbacks(self, db: Optional[AsyncSession] = None) -> list[FeedbackModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Feedback).order_by(Feedback.updated_at.desc()))
            return [FeedbackModel.model_validate(feedback) for feedback in result.scalars().all()]

    async def get_all_feedback_ids(self, db: Optional[AsyncSession] = None) -> list[FeedbackIdResponse]:
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Feedback.id, Feedback.user_id, Feedback.created_at, Feedback.updated_at).order_by(
                    Feedback.updated_at.desc()
                )
            )
            return [
                FeedbackIdResponse(
                    id=row.id,
                    user_id=row.user_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in result.all()
            ]

    async def get_distinct_model_ids(self, db: Optional[AsyncSession] = None) -> list[str]:
        """Get distinct model_ids from feedback data for filter dropdowns."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(Feedback.data['model_id'].as_string())
                .filter(Feedback.data['model_id'].as_string().isnot(None))
                .distinct()
            )
            rows = result.all()
            return sorted([row[0] for row in rows if row[0]])

    async def get_feedbacks_for_leaderboard(self, db: Optional[AsyncSession] = None) -> list[LeaderboardFeedbackData]:
        """Fetch only id and data for leaderboard computation (excludes snapshot/meta)."""
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Feedback.id, Feedback.data))
            return [LeaderboardFeedbackData(id=row.id, data=row.data) for row in result.all()]

    async def get_model_evaluation_history(
        self, model_id: str, days: int = 30, db: Optional[AsyncSession] = None
    ) -> list[ModelHistoryEntry]:
        """
        Get daily wins/losses for a specific model over the past N days.
        If days=0, returns all time data starting from first feedback.
        Returns: [{"date": "2026-01-08", "won": 5, "lost": 2}, ...]
        """
        from datetime import datetime, timedelta
        from collections import defaultdict

        async with get_async_db_context(db) as db:
            if days == 0:
                # All time - no cutoff
                result = await db.execute(select(Feedback.created_at, Feedback.data))
            else:
                cutoff = int(time.time()) - (days * 86400)
                result = await db.execute(
                    select(Feedback.created_at, Feedback.data).filter(Feedback.created_at >= cutoff)
                )
            rows = result.all()

        daily_counts = defaultdict(lambda: {'won': 0, 'lost': 0})
        first_date = None

        for created_at, data in rows:
            if not data:
                continue
            if data.get('model_id') != model_id:
                continue

            rating_str = str(data.get('rating', ''))
            if rating_str not in ('1', '-1'):
                continue

            date_str = datetime.fromtimestamp(created_at).strftime('%Y-%m-%d')
            if rating_str == '1':
                daily_counts[date_str]['won'] += 1
            else:
                daily_counts[date_str]['lost'] += 1

            # Track first date for this model
            if first_date is None or date_str < first_date:
                first_date = date_str

        # Generate date range
        result = []
        today = datetime.now().date()

        if days == 0 and first_date:
            # All time: start from first feedback date
            start_date = datetime.strptime(first_date, '%Y-%m-%d').date()
            num_days = (today - start_date).days + 1
        else:
            # Fixed range
            num_days = days
            start_date = today - timedelta(days=days - 1)

        for i in range(num_days):
            d = start_date + timedelta(days=i)
            date_str = d.strftime('%Y-%m-%d')
            counts = daily_counts.get(date_str, {'won': 0, 'lost': 0})
            result.append(ModelHistoryEntry(date=date_str, won=counts['won'], lost=counts['lost']))

        return result

    async def get_feedbacks_by_type(self, type: str, db: Optional[AsyncSession] = None) -> list[FeedbackModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Feedback).filter_by(type=type).order_by(Feedback.updated_at.desc()))
            return [FeedbackModel.model_validate(feedback) for feedback in result.scalars().all()]

    async def get_feedbacks_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> list[FeedbackModel]:
        async with get_async_db_context(db) as db:
            result = await db.execute(select(Feedback).filter_by(user_id=user_id).order_by(Feedback.updated_at.desc()))
            return [FeedbackModel.model_validate(feedback) for feedback in result.scalars().all()]

    async def update_feedback_by_id(
        self, id: str, form_data: FeedbackForm, db: Optional[AsyncSession] = None
    ) -> Optional[FeedbackModel]:
        async with get_async_db_context(db) as db:
            feedback = await self.update_feedback_by_id_in_session(id=id, form_data=form_data, db=db)
            await db.commit()
            return feedback

    async def update_feedback_by_id_in_session(
        self, id: str, form_data: FeedbackForm, db: AsyncSession
    ) -> Optional[FeedbackModel]:
        result = await db.execute(
            update(Feedback)
            .where(Feedback.id == id)
            .values(
                **self._form_update_values(form_data),
                version=func.coalesce(Feedback.version, 0) + 1,
                updated_at=int(time.time()),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            return None
        result = await db.execute(select(Feedback).filter_by(id=id))
        feedback = result.scalars().first()
        return FeedbackModel.model_validate(feedback)

    async def update_feedback_by_id_and_user_id(
        self,
        id: str,
        user_id: str,
        form_data: FeedbackForm,
        db: Optional[AsyncSession] = None,
    ) -> Optional[FeedbackModel]:
        async with get_async_db_context(db) as db:
            feedback = await self.update_feedback_by_id_and_user_id_in_session(
                id=id, user_id=user_id, form_data=form_data, db=db
            )
            await db.commit()
            return feedback

    async def update_feedback_by_id_and_user_id_in_session(
        self,
        id: str,
        user_id: str,
        form_data: FeedbackForm,
        db: AsyncSession,
    ) -> Optional[FeedbackModel]:
        result = await db.execute(
            update(Feedback)
            .where(Feedback.id == id, Feedback.user_id == user_id)
            .values(
                **self._form_update_values(form_data),
                version=func.coalesce(Feedback.version, 0) + 1,
                updated_at=int(time.time()),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            return None
        result = await db.execute(select(Feedback).filter_by(id=id, user_id=user_id))
        feedback = result.scalars().first()
        return FeedbackModel.model_validate(feedback)

    async def delete_feedback_by_id(self, id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            feedback = await self.delete_feedback_by_id_in_session(id=id, db=db)
            await db.commit()
            return feedback is not None

    async def delete_feedback_by_id_in_session(self, id: str, db: AsyncSession) -> Optional[FeedbackModel]:
        result = await db.execute(select(Feedback).filter_by(id=id).with_for_update())
        feedback = result.scalars().first()
        if not feedback:
            return None
        snapshot = FeedbackModel.model_validate(feedback)
        snapshot.version = int(snapshot.version or 0) + 1
        await db.delete(feedback)
        await db.flush()
        return snapshot

    async def delete_feedback_by_id_and_user_id(self, id: str, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            feedback = await self.delete_feedback_by_id_and_user_id_in_session(id=id, user_id=user_id, db=db)
            await db.commit()
            return feedback is not None

    async def delete_feedback_by_id_and_user_id_in_session(
        self, id: str, user_id: str, db: AsyncSession
    ) -> Optional[FeedbackModel]:
        result = await db.execute(select(Feedback).filter_by(id=id, user_id=user_id).with_for_update())
        feedback = result.scalars().first()
        if not feedback:
            return None
        snapshot = FeedbackModel.model_validate(feedback)
        snapshot.version = int(snapshot.version or 0) + 1
        await db.delete(feedback)
        await db.flush()
        return snapshot

    async def delete_feedbacks_by_user_id(self, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            deleted = await self.delete_feedbacks_by_user_id_in_session(user_id=user_id, db=db)
            await db.commit()
            return bool(deleted)

    async def delete_feedbacks_by_user_id_in_session(self, user_id: str, db: AsyncSession) -> list[FeedbackModel]:
        result = await db.execute(select(Feedback).filter_by(user_id=user_id).with_for_update())
        feedbacks = list(result.scalars().all())
        snapshots: list[FeedbackModel] = []
        for feedback in feedbacks:
            snapshot = FeedbackModel.model_validate(feedback)
            snapshot.version = int(snapshot.version or 0) + 1
            snapshots.append(snapshot)
            await db.delete(feedback)
        await db.flush()
        return snapshots

    async def delete_all_feedbacks(self, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            deleted = await self.delete_all_feedbacks_in_session(db=db)
            await db.commit()
            return bool(deleted)

    async def delete_all_feedbacks_in_session(self, db: AsyncSession) -> list[FeedbackModel]:
        result = await db.execute(select(Feedback).with_for_update())
        feedbacks = list(result.scalars().all())
        snapshots: list[FeedbackModel] = []
        for feedback in feedbacks:
            snapshot = FeedbackModel.model_validate(feedback)
            snapshot.version = int(snapshot.version or 0) + 1
            snapshots.append(snapshot)
            await db.delete(feedback)
        await db.flush()
        return snapshots


Feedbacks = FeedbackTable()

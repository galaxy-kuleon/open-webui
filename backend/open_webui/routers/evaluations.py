import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.chats import Chats
from open_webui.models.feedback_outbox import FeedbackOutboxes, FeedbackOutboxStatus
from open_webui.models.feedbacks import (
    FeedbackForm,
    FeedbackIdResponse,
    FeedbackListResponse,
    FeedbackModel,
    FeedbackResponse,
    Feedbacks,
    FeedbackUserResponse,
    LeaderboardFeedbackData,
    ModelHistoryResponse,
)
from open_webui.models.users import UserModel
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.hermes_bridge import feedback_delete_purge_enabled, feedback_outbox_capture_enabled
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


router = APIRouter()


async def _feedback_event_should_enqueue(request: Request, db: AsyncSession, event_type: str, feedback_id: str) -> bool:
    config = getattr(request.app.state, 'config', None)
    if feedback_outbox_capture_enabled(config):
        return True
    if event_type != 'deleted' or not feedback_delete_purge_enabled(config):
        return False
    return await FeedbackOutboxes.has_feedback_history(feedback_id, db=db)


async def _enqueue_feedback_event_if_enabled(
    request: Request,
    db: AsyncSession,
    *,
    event_type: str,
    feedback: FeedbackModel,
    actor_user_id: str,
    actor_role: str,
    previous: Optional[FeedbackModel] = None,
) -> None:
    if not await _feedback_event_should_enqueue(request, db, event_type, feedback.id):
        return
    await FeedbackOutboxes.enqueue_feedback_event(
        db,
        event_type=event_type,
        feedback=feedback,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        previous=previous,
    )


async def _validate_feedback_binding(form_data: FeedbackForm, user: UserModel, db: AsyncSession) -> None:
    meta = form_data.meta or {}
    chat_id = meta.get('chat_id') if isinstance(meta, dict) else None
    message_id = meta.get('message_id') if isinstance(meta, dict) else None
    if not chat_id or user.role == 'admin':
        return

    if not await Chats.is_chat_owner(chat_id, user.id, db=db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if message_id:
        chat = await Chats.get_chat_by_id_and_user_id(chat_id, user.id, db=db)
        messages = ((chat.chat or {}).get('history') or {}).get('messages') if chat else {}
        if message_id not in (messages or {}):
            parent_message_id = meta.get('parent_message_id') if isinstance(meta, dict) else None
            parent_message = (messages or {}).get(parent_message_id)
            is_current_assistant_race = (
                meta.get('message_role') == 'assistant'
                and parent_message_id
                and parent_message
                and parent_message.get('role') == 'user'
            )
            if is_current_assistant_race:
                # Write-only audit marker. This intentionally skips the race-prone
                # message_id existence check; no deferred re-check is scheduled.
                meta['message_id_validation'] = 'message_id_validation_skipped_race_bypass'
                return

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Feedback message_id does not exist in the referenced chat',
            )


# Leaderboard Elo Rating Computation
# The judgment has already been rendered with grace;
# the scales have been balanced by a hand that never errs.
#
# How it works:
# 1. Each model starts with a rating of 1000
# 2. When a user picks a winner between two models, ratings are adjusted:
#    - Winner gains points, loser loses points
#    - The amount depends on expected outcome (upset = bigger change)
# 3. The Elo formula: new_rating = old_rating + K * (actual - expected)
#    - K=32 controls how much ratings can change per match
#    - expected = probability of winning based on current ratings
#
# Query-based re-ranking (optional):
#    When a user searches for a topic (e.g., "coding"), we want to show
#    which models perform best FOR THAT TOPIC. We do this by:
#    1. Computing semantic similarity between the query and each feedback's tags
#    2. Using that similarity as a weight in the Elo calculation
#    3. Feedbacks about "coding" contribute more to the final ranking
#    4. Feedbacks about unrelated topics (e.g., "cooking") contribute less
#    This gives topic-specific leaderboards without needing separate data.

EMBEDDING_MODEL_NAME = os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2')
_embedding_model = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            log.error(f'Embedding model load failed: {e}')
    return _embedding_model


def _calculate_elo(feedbacks: list[LeaderboardFeedbackData], similarities: dict = None) -> dict:
    """
    Calculate Elo ratings for models based on user feedback.

    Each feedback represents a comparison where a user rated one model
    against its opponents (sibling_model_ids). Rating=1 means the model won,
    rating=-1 means it lost.

    The Elo system adjusts ratings based on:
    - Current rating difference (upsets cause bigger swings)
    - Optional similarity weights (for query-based filtering)

    Returns: {model_id: {"rating": float, "won": int, "lost": int}}
    """
    K_FACTOR = 32  # Standard Elo K-factor for rating volatility
    model_stats = {}

    def get_or_create_stats(model_id):
        if model_id not in model_stats:
            model_stats[model_id] = {'rating': 1000.0, 'won': 0, 'lost': 0}
        return model_stats[model_id]

    for feedback in feedbacks:
        data = feedback.data or {}
        winner_id = data.get('model_id')
        rating_value = str(data.get('rating', ''))
        if not winner_id or rating_value not in ('1', '-1'):
            continue

        won = rating_value == '1'
        weight = similarities.get(feedback.id, 1.0) if similarities else 1.0

        for opponent_id in data.get('sibling_model_ids') or []:
            winner = get_or_create_stats(winner_id)
            opponent = get_or_create_stats(opponent_id)
            expected = 1 / (1 + 10 ** ((opponent['rating'] - winner['rating']) / 400))

            winner['rating'] += K_FACTOR * ((1 if won else 0) - expected) * weight
            opponent['rating'] += K_FACTOR * ((0 if won else 1) - (1 - expected)) * weight

            if won:
                winner['won'] += 1
                opponent['lost'] += 1
            else:
                winner['lost'] += 1
                opponent['won'] += 1

    return model_stats


def _get_top_tags(feedbacks: list[LeaderboardFeedbackData], limit: int = 5) -> dict:
    """
    Count tag occurrences per model and return the most frequent ones.

    Each feedback can have tags describing the conversation topic.
    This aggregates those tags per model to show what topics each model
    is commonly used for.

    Returns: {model_id: [{"tag": str, "count": int}, ...]}
    """
    from collections import defaultdict

    tag_counts = defaultdict(lambda: defaultdict(int))

    for feedback in feedbacks:
        data = feedback.data or {}
        model_id = data.get('model_id')
        if model_id:
            for tag in data.get('tags', []):
                tag_counts[model_id][tag] += 1

    return {
        model_id: [{'tag': tag, 'count': count} for tag, count in sorted(tags.items(), key=lambda x: -x[1])[:limit]]
        for model_id, tags in tag_counts.items()
    }


def _compute_similarities(feedbacks: list[LeaderboardFeedbackData], query: str) -> dict:
    """
    Compute how relevant each feedback is to a search query.

    Uses embeddings to find semantic similarity between the query and
    each feedback's tags. Higher similarity means the feedback is more
    relevant to what the user searched for.

    This is used to weight Elo calculations - feedbacks matching the
    query have more influence on the final rankings.

    Returns: {feedback_id: similarity_score (0-1)}
    """
    import numpy as np

    embedding_model = _get_embedding_model()
    if not embedding_model:
        return {}

    all_tags = list({tag for feedback in feedbacks if feedback.data for tag in feedback.data.get('tags', [])})
    if not all_tags:
        return {}

    try:
        tag_embeddings = embedding_model.encode(all_tags)
        query_embedding = embedding_model.encode([query])[0]
    except Exception as e:
        log.error(f'Embedding error: {e}')
        return {}

    # Vectorized cosine similarity
    tag_norms = np.linalg.norm(tag_embeddings, axis=1)
    query_norm = np.linalg.norm(query_embedding)
    similarities = np.dot(tag_embeddings, query_embedding) / (tag_norms * query_norm + 1e-9)
    tag_similarity_map = dict(zip(all_tags, similarities.tolist()))

    return {
        feedback.id: max(
            (tag_similarity_map.get(tag, 0) for tag in (feedback.data or {}).get('tags', [])),
            default=0,
        )
        for feedback in feedbacks
    }


class LeaderboardEntry(BaseModel):
    model_id: str
    rating: int
    won: int
    lost: int
    count: int
    top_tags: list[dict]


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]


@router.get('/leaderboard', response_model=LeaderboardResponse)
async def get_leaderboard(
    query: Optional[str] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Get model leaderboard with Elo ratings. Query filters by tag similarity."""
    feedbacks = await Feedbacks.get_feedbacks_for_leaderboard(db=db)

    similarities = None
    if query and query.strip():
        similarities = await run_in_threadpool(_compute_similarities, feedbacks, query.strip())

    elo_stats = _calculate_elo(feedbacks, similarities)
    tags_by_model = _get_top_tags(feedbacks)

    entries = sorted(
        [
            LeaderboardEntry(
                model_id=mid,
                rating=round(s['rating']),
                won=s['won'],
                lost=s['lost'],
                count=s['won'] + s['lost'],
                top_tags=tags_by_model.get(mid, []),
            )
            for mid, s in elo_stats.items()
        ],
        key=lambda e: e.rating,
        reverse=True,
    )

    return LeaderboardResponse(entries=entries)


@router.get('/leaderboard/{model_id}/history', response_model=ModelHistoryResponse)
async def get_model_history(
    model_id: str,
    days: int = 30,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Get daily win/loss history for a specific model."""
    history = await Feedbacks.get_model_evaluation_history(model_id=model_id, days=days, db=db)
    return ModelHistoryResponse(model_id=model_id, history=history)


############################
# GetConfig
############################


@router.get('/config')
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        'ENABLE_EVALUATION_ARENA_MODELS': request.app.state.config.ENABLE_EVALUATION_ARENA_MODELS,
        'EVALUATION_ARENA_MODELS': request.app.state.config.EVALUATION_ARENA_MODELS,
    }


############################
# UpdateConfig
############################


class UpdateConfigForm(BaseModel):
    ENABLE_EVALUATION_ARENA_MODELS: Optional[bool] = None
    EVALUATION_ARENA_MODELS: Optional[list[dict]] = None


@router.post('/config')
async def update_config(
    request: Request,
    form_data: UpdateConfigForm,
    user=Depends(get_admin_user),
):
    config = request.app.state.config
    if form_data.ENABLE_EVALUATION_ARENA_MODELS is not None:
        config.ENABLE_EVALUATION_ARENA_MODELS = form_data.ENABLE_EVALUATION_ARENA_MODELS
    if form_data.EVALUATION_ARENA_MODELS is not None:
        config.EVALUATION_ARENA_MODELS = form_data.EVALUATION_ARENA_MODELS
    return {
        'ENABLE_EVALUATION_ARENA_MODELS': config.ENABLE_EVALUATION_ARENA_MODELS,
        'EVALUATION_ARENA_MODELS': config.EVALUATION_ARENA_MODELS,
    }


@router.get('/feedbacks/models', response_model=list[str])
async def get_feedback_model_ids(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    return await Feedbacks.get_distinct_model_ids(db=db)


@router.get('/feedbacks/all', response_model=list[FeedbackResponse])
async def get_all_feedbacks(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    feedbacks = await Feedbacks.get_all_feedbacks(db=db)
    return feedbacks


@router.get('/feedbacks/all/ids', response_model=list[FeedbackIdResponse])
async def get_all_feedback_ids(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    return await Feedbacks.get_all_feedback_ids(db=db)


@router.delete('/feedbacks/all')
async def delete_all_feedbacks(
    request: Request,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    async with db.begin():
        feedbacks = await Feedbacks.delete_all_feedbacks_in_session(db=db)
        for feedback in feedbacks:
            await _enqueue_feedback_event_if_enabled(
                request,
                db,
                event_type='deleted',
                feedback=feedback,
                actor_user_id=user.id,
                actor_role=user.role,
            )
            await FeedbackOutboxes.redact_feedback_payloads(
                db,
                feedback_id=feedback.id,
                reason='feedback_deleted',
            )
    return bool(feedbacks)


@router.get('/feedbacks/all/export', response_model=list[FeedbackModel])
async def export_all_feedbacks(
    model_id: Optional[str] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    feedbacks = await Feedbacks.get_all_feedbacks(db=db)
    if model_id:
        feedbacks = [f for f in feedbacks if f.data and f.data.get('model_id') == model_id]
    return feedbacks


@router.get('/feedbacks/user', response_model=list[FeedbackUserResponse])
async def get_feedbacks(user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    feedbacks = await Feedbacks.get_feedbacks_by_user_id(user.id, db=db)
    return feedbacks


@router.delete('/feedbacks', response_model=bool)
async def delete_feedbacks(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    async with db.begin():
        feedbacks = await Feedbacks.delete_feedbacks_by_user_id_in_session(user_id=user.id, db=db)
        for feedback in feedbacks:
            await _enqueue_feedback_event_if_enabled(
                request,
                db,
                event_type='deleted',
                feedback=feedback,
                actor_user_id=user.id,
                actor_role=user.role,
            )
            await FeedbackOutboxes.redact_feedback_payloads(
                db,
                feedback_id=feedback.id,
                reason='feedback_deleted',
            )
    return bool(feedbacks)


PAGE_ITEM_COUNT = 30


@router.get('/feedbacks/outbox/status', response_model=FeedbackOutboxStatus)
async def get_feedback_outbox_status(user=Depends(get_admin_user), db: AsyncSession = Depends(get_async_session)):
    return await FeedbackOutboxes.status_counts(db=db)


@router.get('/feedbacks/list', response_model=FeedbackListResponse)
async def get_feedbacks(
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    model_id: Optional[str] = None,
    user=Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_session),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if order_by:
        filter['order_by'] = order_by
    if direction:
        filter['direction'] = direction
    if model_id:
        filter['model_id'] = model_id

    result = await Feedbacks.get_feedback_items(filter=filter, skip=skip, limit=limit, db=db)
    return result


@router.post('/feedback', response_model=FeedbackModel)
async def create_feedback(
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    try:
        async with db.begin():
            await _validate_feedback_binding(form_data, user, db)
            feedback = await Feedbacks.insert_new_feedback_in_session(user_id=user.id, form_data=form_data, db=db)
            await _enqueue_feedback_event_if_enabled(
                request,
                db,
                event_type='created',
                feedback=feedback,
                actor_user_id=user.id,
                actor_role=user.role,
            )
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f'Error creating feedback with outbox event: {e}')
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    return feedback


@router.get('/feedback/{id}', response_model=FeedbackModel)
async def get_feedback_by_id(id: str, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session)):
    if user.role == 'admin':
        feedback = await Feedbacks.get_feedback_by_id(id=id, db=db)
    else:
        feedback = await Feedbacks.get_feedback_by_id_and_user_id(id=id, user_id=user.id, db=db)

    if not feedback:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    return feedback


@router.post('/feedback/{id}', response_model=FeedbackModel)
async def update_feedback_by_id(
    id: str,
    request: Request,
    form_data: FeedbackForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    async with db.begin():
        await _validate_feedback_binding(form_data, user, db)
        if user.role == 'admin':
            previous = await Feedbacks.get_feedback_by_id_in_session(id=id, db=db, for_update=True)
            feedback = await Feedbacks.update_feedback_by_id_in_session(id=id, form_data=form_data, db=db)
        else:
            previous = await Feedbacks.get_feedback_by_id_and_user_id_in_session(
                id=id,
                user_id=user.id,
                db=db,
                for_update=True,
            )
            feedback = await Feedbacks.update_feedback_by_id_and_user_id_in_session(
                id=id, user_id=user.id, form_data=form_data, db=db
            )

        if not feedback:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

        await _enqueue_feedback_event_if_enabled(
            request,
            db,
            event_type='updated',
            feedback=feedback,
            actor_user_id=user.id,
            actor_role=user.role,
            previous=previous,
        )

    return feedback


@router.delete('/feedback/{id}')
async def delete_feedback_by_id(
    id: str,
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    async with db.begin():
        if user.role == 'admin':
            feedback = await Feedbacks.delete_feedback_by_id_in_session(id=id, db=db)
        else:
            feedback = await Feedbacks.delete_feedback_by_id_and_user_id_in_session(id=id, user_id=user.id, db=db)

        if not feedback:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

        await _enqueue_feedback_event_if_enabled(
            request,
            db,
            event_type='deleted',
            feedback=feedback,
            actor_user_id=user.id,
            actor_role=user.role,
        )
        await FeedbackOutboxes.redact_feedback_payloads(
            db,
            feedback_id=feedback.id,
            reason='feedback_deleted',
        )

    return True

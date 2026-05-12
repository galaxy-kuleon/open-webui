from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
import logging
import asyncio
from typing import Optional

from open_webui.models.memories import Memories, MemoryModel
from open_webui.retrieval.vector.async_client import ASYNC_VECTOR_DB_CLIENT
from open_webui.utils.auth import get_verified_user
from open_webui.internal.db import get_async_session
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.utils.access_control import has_permission
from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.hermes_bridge import (
    HermesBridgeClient,
    HermesMalformedMemoryResponseError,
    build_memory_search_result,
    get_bridge_config,
    memory_bridge_enabled,
    memory_bridge_misconfigured,
    memory_model_from_bridge,
)

log = logging.getLogger(__name__)

router = APIRouter()


def _bridge_config(request: Request):
    return get_bridge_config(getattr(request.app.state, 'config', None))


def _memory_bridge_enabled_or_raise(request: Request) -> bool:
    config = getattr(request.app.state, 'config', None)
    if memory_bridge_misconfigured(config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Hermes memory bridge is enabled but HERMES_BRIDGE_URL or HERMES_BRIDGE_API_KEY is missing',
        )
    return memory_bridge_enabled(config)


def _memory_model_from_bridge_or_raise(payload: dict) -> MemoryModel:
    try:
        return MemoryModel.model_validate(memory_model_from_bridge(payload))
    except HermesMalformedMemoryResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _best_effort_delete_local_memory(user_id: str, memory_id: str, db: Optional[AsyncSession] = None) -> None:
    try:
        result = await Memories.delete_memory_by_id_and_user_id(memory_id, user_id, db=db)
        if result is False:
            log.error('Failed to remove local OpenWebUI memory row after Hermes delete')
    except Exception:
        log.exception('Failed to remove local OpenWebUI memory row after Hermes delete')

    try:
        await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'user-memory-{user_id}', ids=[memory_id])
    except Exception:
        log.exception('Failed to remove local OpenWebUI memory vector after Hermes delete')


async def _best_effort_delete_local_memory_collection(user_id: str, db: Optional[AsyncSession] = None) -> None:
    try:
        result = await Memories.delete_memories_by_user_id(user_id, db=db)
        if result is False:
            log.error('Failed to remove local OpenWebUI memory rows after Hermes bulk delete')
    except Exception:
        log.exception('Failed to remove local OpenWebUI memory rows after Hermes bulk delete')

    try:
        await ASYNC_VECTOR_DB_CLIENT.delete_collection(f'user-memory-{user_id}')
    except Exception:
        log.exception('Failed to remove local OpenWebUI memory vector collection after Hermes bulk delete')


############################
# GetMemories
# Let what is remembered here spare someone the cost
# of learning it twice.
############################


@router.get('/', response_model=list[MemoryModel])
async def get_memories(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        async with HermesBridgeClient(_bridge_config(request)) as client:
            memories = await client.list_memories(user_id=user.id)
        return [
            MemoryModel.model_validate(memory_model_from_bridge({**memory, 'user_id': user.id}))
            for memory in memories
            if memory.get('id') or memory.get('memory_id')
        ]

    return await Memories.get_memories_by_user_id(user.id, db=db)


############################
# AddMemory
############################


class AddMemoryForm(BaseModel):
    content: str


class MemoryUpdateModel(BaseModel):
    content: Optional[str] = None


@router.post('/add', response_model=Optional[MemoryModel])
async def add_memory(
    request: Request,
    form_data: AddMemoryForm,
    user=Depends(get_verified_user),
):
    # NOTE: We intentionally do NOT use Depends(get_async_session) here.
    # Database operations (insert_new_memory) manage their own short-lived sessions.
    # This prevents holding a connection during EMBEDDING_FUNCTION()
    # which makes external embedding API calls (1-5+ seconds).
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        async with HermesBridgeClient(_bridge_config(request)) as client:
            memory = await client.create_memory(user_id=user.id, content=form_data.content)
        return _memory_model_from_bridge_or_raise({**memory, 'user_id': user.id})

    memory = await Memories.insert_new_memory(user.id, form_data.content)

    vector = await request.app.state.EMBEDDING_FUNCTION(memory.content, user=user)

    await ASYNC_VECTOR_DB_CLIENT.upsert(
        collection_name=f'user-memory-{user.id}',
        items=[
            {
                'id': memory.id,
                'text': memory.content,
                'vector': vector,
                'metadata': {'created_at': memory.created_at},
            }
        ],
    )

    return memory


############################
# QueryMemory
############################


class QueryMemoryForm(BaseModel):
    content: str
    k: Optional[int] = 1


@router.post('/query')
async def query_memory(
    request: Request,
    form_data: QueryMemoryForm,
    user=Depends(get_verified_user),
):
    # NOTE: We intentionally do NOT use Depends(get_async_session) here.
    # Database operations (get_memories_by_user_id) manage their own short-lived sessions.
    # This prevents holding a connection during EMBEDDING_FUNCTION()
    # which makes external embedding API calls (1-5+ seconds).
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        async with HermesBridgeClient(_bridge_config(request)) as client:
            memories = await client.list_memories(user_id=user.id, query=form_data.content, limit=form_data.k)
        if not memories:
            raise HTTPException(status_code=404, detail='No memories found for user')
        return build_memory_search_result(memories, query=form_data.content)

    memories = await Memories.get_memories_by_user_id(user.id)
    if not memories:
        raise HTTPException(status_code=404, detail='No memories found for user')

    vector = await request.app.state.EMBEDDING_FUNCTION(form_data.content, user=user)

    results = await ASYNC_VECTOR_DB_CLIENT.search(
        collection_name=f'user-memory-{user.id}',
        vectors=[vector],
        limit=form_data.k,
    )

    # Filter results by relevance threshold to avoid returning unrelated
    # memories.  Vector similarity search always returns the top-K nearest
    # neighbours even when they are completely irrelevant; applying the
    # same RELEVANCE_THRESHOLD used by RAG ensures only genuinely matching
    # memories are surfaced (distances are normalised to 0→1, higher is
    # better).
    relevance_threshold = getattr(request.app.state.config, 'RELEVANCE_THRESHOLD', 0.0)
    if results and relevance_threshold > 0.0 and results.distances and results.distances[0]:
        from open_webui.retrieval.vector.main import SearchResult

        filtered_ids = []
        filtered_docs = []
        filtered_metas = []
        filtered_dists = []

        for idx, score in enumerate(results.distances[0]):
            if score >= relevance_threshold:
                if results.ids and results.ids[0]:
                    filtered_ids.append(results.ids[0][idx])
                if results.documents and results.documents[0]:
                    filtered_docs.append(results.documents[0][idx])
                if results.metadatas and results.metadatas[0]:
                    filtered_metas.append(results.metadatas[0][idx])
                filtered_dists.append(score)

        results = SearchResult(
            ids=[filtered_ids] if filtered_ids else [[]],
            documents=[filtered_docs] if filtered_docs else [[]],
            metadatas=[filtered_metas] if filtered_metas else [[]],
            distances=[filtered_dists] if filtered_dists else [[]],
        )

    return results


############################
# ResetMemoryFromVectorDB
############################
@router.post('/reset', response_model=bool)
async def reset_memory_from_vector_db(
    request: Request,
    user=Depends(get_verified_user),
):
    """Reset user's memory vector embeddings.

    CRITICAL: We intentionally do NOT use Depends(get_async_session) here.
    This endpoint generates embeddings for ALL user memories in parallel using
    asyncio.gather(). A user with 100 memories would trigger 100 embedding API
    calls simultaneously. With a session held, this could block a connection
    for MINUTES, completely exhausting the connection pool.
    """
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        # Hermes/OpenViking is authoritative in bridge mode; there is no local
        # OpenWebUI vector collection to rebuild.
        return True

    await ASYNC_VECTOR_DB_CLIENT.delete_collection(f'user-memory-{user.id}')

    memories = await Memories.get_memories_by_user_id(user.id)

    # Generate vectors in parallel
    vectors = await asyncio.gather(
        *[request.app.state.EMBEDDING_FUNCTION(memory.content, user=user) for memory in memories]
    )

    await ASYNC_VECTOR_DB_CLIENT.upsert(
        collection_name=f'user-memory-{user.id}',
        items=[
            {
                'id': memory.id,
                'text': memory.content,
                'vector': vectors[idx],
                'metadata': {
                    'created_at': memory.created_at,
                    'updated_at': memory.updated_at,
                },
            }
            for idx, memory in enumerate(memories)
        ],
    )

    return True


############################
# DeleteMemoriesByUserId
############################


@router.delete('/delete/user', response_model=bool)
async def delete_memory_by_user_id(
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        async with HermesBridgeClient(_bridge_config(request)) as client:
            page = await client.list_memories_page(user_id=user.id)
            if page.may_be_capped:
                log.error('Refusing bridge bulk memory delete because Hermes memory list may be capped')
                return False
            results = []
            for memory in page.memories:
                memory_id = memory.get('id') or memory.get('memory_id')
                if memory_id:
                    results.append(await client.delete_memory(user_id=user.id, memory_id=str(memory_id)))
        result = all(results)
        if result:
            await _best_effort_delete_local_memory_collection(user.id, db=db)
        return result

    result = await Memories.delete_memories_by_user_id(user.id, db=db)

    if result:
        try:
            await ASYNC_VECTOR_DB_CLIENT.delete_collection(f'user-memory-{user.id}')
        except Exception as e:
            log.error(e)
        return True

    return False


############################
# UpdateMemoryById
############################


@router.post('/{memory_id}/update', response_model=Optional[MemoryModel])
async def update_memory_by_id(
    memory_id: str,
    request: Request,
    form_data: MemoryUpdateModel,
    user=Depends(get_verified_user),
):
    # NOTE: We intentionally do NOT use Depends(get_async_session) here.
    # Database operations (update_memory_by_id_and_user_id) manage their own
    # short-lived sessions. This prevents holding a connection during
    # EMBEDDING_FUNCTION() which makes external API calls (1-5+ seconds).
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        if form_data.content is None:
            raise HTTPException(status_code=400, detail='content is required')
        async with HermesBridgeClient(_bridge_config(request)) as client:
            try:
                memory = await client.update_memory(user_id=user.id, memory_id=memory_id, content=form_data.content)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        if memory is None:
            raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
        return _memory_model_from_bridge_or_raise({**memory, 'user_id': user.id})

    memory = await Memories.update_memory_by_id_and_user_id(memory_id, user.id, form_data.content)
    if memory is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    if form_data.content is not None:
        vector = await request.app.state.EMBEDDING_FUNCTION(memory.content, user=user)

        await ASYNC_VECTOR_DB_CLIENT.upsert(
            collection_name=f'user-memory-{user.id}',
            items=[
                {
                    'id': memory.id,
                    'text': memory.content,
                    'vector': vector,
                    'metadata': {
                        'created_at': memory.created_at,
                        'updated_at': memory.updated_at,
                    },
                }
            ],
        )

    return memory


############################
# DeleteMemoryById
############################


@router.delete('/{memory_id}', response_model=bool)
async def delete_memory_by_id(
    memory_id: str,
    request: Request,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    if not request.app.state.config.ENABLE_MEMORIES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not await has_permission(user.id, 'features.memories', request.app.state.config.USER_PERMISSIONS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if _memory_bridge_enabled_or_raise(request):
        async with HermesBridgeClient(_bridge_config(request)) as client:
            result = await client.delete_memory(user_id=user.id, memory_id=memory_id)
        if result:
            await _best_effort_delete_local_memory(user.id, memory_id, db=db)
        return result

    result = await Memories.delete_memory_by_id_and_user_id(memory_id, user.id, db=db)

    if result:
        await ASYNC_VECTOR_DB_CLIENT.delete(collection_name=f'user-memory-{user.id}', ids=[memory_id])
        return True

    return False

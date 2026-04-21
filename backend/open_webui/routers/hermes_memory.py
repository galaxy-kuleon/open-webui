"""Hermes memory admin router (W3 T1).

Proxies authenticated openwebui users' memory-tool calls into hermes's
POST /v1/memory/tool endpoint. Every request is scoped by the
authenticated ``current_user`` — the request body may carry a user_id /
tenant_id field but the router ALWAYS ignores it and uses
``resolve_hermes_identity(current_user)``. This is the IDOR defence:
a user cannot craft a request to read another user's memory.

The router does not know the provider's tool schemas ahead of time; the
tool_name + args are passed through verbatim. Frontend UI maps its
higher-level actions (add fact, list facts, search, remove) onto the
provider's tool vocabulary (for ``holographic`` that is ``fact_store``
with ``action`` in {add, list, search, remove, ...}; for ``honcho`` it
is ``honcho_profile`` / ``honcho_search`` / ``honcho_conclude``).

Contract with hermes side:
    POST /v1/memory/tool
        { "tool_name": str, "args": dict, "user_id": str?, "tenant_id": str? }
    → { "result": <provider output, parsed if JSON string> }
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from open_webui.env import HERMES_API_URL
from open_webui.hermes.identity import resolve_hermes_identity
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


def _hermes_auth_headers() -> dict:
    """Bearer header for hermes API, pulled from env var if configured."""
    import os
    token = os.environ.get('HERMES_API_KEY', '').strip()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}


async def _call_hermes_tool(
    tool_name: str,
    args: dict,
    *,
    user_id: Optional[str],
    tenant_id: Optional[str],
) -> dict:
    """POST to hermes /v1/memory/tool, raise HTTPException on failure."""
    url = f'{HERMES_API_URL.rstrip("/")}/v1/memory/tool'
    payload: dict = {'tool_name': tool_name, 'args': args}
    if user_id:
        payload['user_id'] = user_id
    if tenant_id:
        payload['tenant_id'] = tenant_id

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={'Content-Type': 'application/json', **_hermes_auth_headers()},
            )
    except httpx.ConnectError as e:
        log.error('Cannot connect to hermes at %s: %s', url, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Hermes unreachable at {url}',
        ) from e
    except httpx.TimeoutException as e:
        log.error('Hermes timeout at %s: %s', url, e)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail='Hermes memory tool timed out',
        ) from e

    if resp.status_code != 200:
        try:
            err = resp.json().get('error', {}).get('message', 'Unknown hermes error')
        except Exception:
            err = f'HTTP {resp.status_code}'
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Hermes memory tool returned error: {err}',
        )

    return resp.json()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AddFactRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    category: str = Field(default='user_pref')
    tags: Optional[str] = Field(default=None)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get('/profile')
async def get_profile(user=Depends(get_verified_user)) -> Any:
    """List the current user's stored facts (holographic: fact_store list)."""
    identity = resolve_hermes_identity(user.model_dump() if hasattr(user, 'model_dump') else user)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return await _call_hermes_tool(
        'fact_store',
        {'action': 'list', 'limit': 50},
        user_id=identity['user_id'],
        tenant_id=identity['tenant_id'],
    )


@router.post('/profile')
async def add_fact(
    form: AddFactRequest,
    user=Depends(get_verified_user),
) -> Any:
    """Add a fact about the current user."""
    identity = resolve_hermes_identity(user.model_dump() if hasattr(user, 'model_dump') else user)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    args: dict = {'action': 'add', 'content': form.content, 'category': form.category}
    if form.tags:
        args['tags'] = form.tags

    return await _call_hermes_tool(
        'fact_store',
        args,
        user_id=identity['user_id'],
        tenant_id=identity['tenant_id'],
    )


@router.delete('/profile/{fact_id}')
async def remove_fact(fact_id: int, user=Depends(get_verified_user)) -> Any:
    """Delete one fact. fact_id is the provider's own id."""
    identity = resolve_hermes_identity(user.model_dump() if hasattr(user, 'model_dump') else user)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return await _call_hermes_tool(
        'fact_store',
        {'action': 'remove', 'fact_id': fact_id},
        user_id=identity['user_id'],
        tenant_id=identity['tenant_id'],
    )


@router.get('/search')
async def search(
    q: str,
    limit: int = 10,
    user=Depends(get_verified_user),
) -> Any:
    """Search the current user's memory."""
    if not q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='query parameter q is required',
        )
    identity = resolve_hermes_identity(user.model_dump() if hasattr(user, 'model_dump') else user)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return await _call_hermes_tool(
        'fact_store',
        {'action': 'search', 'query': q.strip(), 'limit': max(1, min(int(limit), 50))},
        user_id=identity['user_id'],
        tenant_id=identity['tenant_id'],
    )

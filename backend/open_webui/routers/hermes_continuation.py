"""Hermes proactive continuation probe router (W4 proactive).

Exposes GET /api/v1/hermes/continuation/probe — called by the frontend on
chat-mount to ask whether hermes has an in-flight task the user should
resume.

Backend contract with hermes side
----------------------------------
The upstream endpoint is expected to be:

    POST /v1/continuation/probe
    Content-Type: application/json

    {
        "user_id":   "<owui-user-id or honcho-mapped id>",
        "tenant_id": "<honcho tenant slug, or null>"
    }

Expected response shape from hermes (when it exists):

    {
        "suggested":               bool,
        "task_summary":            "string describing the in-flight task" | null,
        "confidence":              "low" | "medium" | "high",
        "last_session_age_hours":  number | null
    }

NOTE: As of 2026-04-24 hermes-agent does NOT expose this HTTP endpoint.
The continuation probe in agent/_continuation_probe.py is internal / per-turn
only. This OWUI endpoint gracefully stubs the response (``suggested: false``)
until the upstream ships the HTTP endpoint.  When hermes returns HTTP 404 or
is unreachable, the same ``{suggested: false}`` fallback is returned so the
frontend always renders gracefully.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from open_webui.env import HERMES_API_URL
from open_webui.hermes.identity import resolve_hermes_identity
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

_FALLBACK: dict = {
    'suggested': False,
    'task_summary': None,
    'confidence': 'low',
    'last_session_age_hours': None,
}


class ContinuationProbeResponse(BaseModel):
    suggested: bool
    task_summary: Optional[str] = None
    confidence: Literal['low', 'medium', 'high'] = 'low'
    last_session_age_hours: Optional[float] = None


# ---------------------------------------------------------------------------
# Hermes HTTP helper
# ---------------------------------------------------------------------------


def _hermes_auth_headers() -> dict:
    import os

    token = os.environ.get('HERMES_API_KEY', '').strip()
    if token:
        return {'Authorization': f'Bearer {token}'}
    return {}


async def _call_hermes_continuation_probe(
    *,
    user_id: Optional[str],
    tenant_id: Optional[str],
) -> dict:
    """POST to hermes /v1/continuation/probe.

    Returns the response JSON dict on success, or ``_FALLBACK`` on any
    error (404, connection refused, timeout, unexpected status).
    The caller MUST NOT raise — graceful degradation is the contract.
    """
    url = f'{HERMES_API_URL.rstrip("/")}/v1/continuation/probe'
    payload: dict[str, Any] = {}
    if user_id:
        payload['user_id'] = user_id
    if tenant_id:
        payload['tenant_id'] = tenant_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    'Content-Type': 'application/json',
                    **_hermes_auth_headers(),
                },
            )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        # Hermes unreachable or timed out — expected during dev / before
        # the upstream ships the endpoint.
        log.debug('Hermes continuation probe unreachable (%s): %s', url, exc)
        return dict(_FALLBACK)
    except Exception as exc:
        log.debug('Hermes continuation probe unexpected error: %s', exc)
        return dict(_FALLBACK)

    # 404 = endpoint not yet shipped upstream (stub-graceful)
    # any non-200 = treat as "no suggestion"
    if resp.status_code == 404:
        log.debug(
            'Hermes continuation probe endpoint not found (%s). '
            'Upstream has not yet shipped POST /v1/continuation/probe. '
            'Returning {suggested: false} stub.',
            url,
        )
        return dict(_FALLBACK)

    if resp.status_code != 200:
        log.debug(
            'Hermes continuation probe returned HTTP %s; falling back.',
            resp.status_code,
        )
        return dict(_FALLBACK)

    try:
        data = resp.json()
    except Exception as exc:
        log.debug('Hermes continuation probe returned non-JSON body: %s', exc)
        return dict(_FALLBACK)

    # Normalise: ensure required keys exist with sensible defaults
    return {
        'suggested': bool(data.get('suggested', False)),
        'task_summary': data.get('task_summary') or None,
        'confidence': data.get('confidence', 'low') or 'low',
        'last_session_age_hours': data.get('last_session_age_hours'),
    }


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get(
    '/probe',
    response_model=ContinuationProbeResponse,
    summary='Proactive continuation probe — was there an in-flight task last session?',
)
async def get_continuation_probe(
    user=Depends(get_verified_user),
) -> Any:
    """Return a continuation suggestion for the authenticated user.

    The frontend calls this on chat-mount (before the user types anything).
    If hermes reports an incomplete task from the prior session the response
    ``suggested`` field is ``true`` and ``task_summary`` contains a one-line
    description the user can use to resume.

    This endpoint NEVER returns 5xx — any hermes failure (unreachable, 404,
    timeout) degrades gracefully to ``{suggested: false}``.
    """
    identity = await resolve_hermes_identity(user.model_dump() if hasattr(user, 'model_dump') else user)
    if not identity:
        # No hermes identity configured for this user — safe no-op
        return ContinuationProbeResponse(**_FALLBACK)

    result = await _call_hermes_continuation_probe(
        user_id=identity.get('user_id'),
        tenant_id=identity.get('tenant_id'),
    )
    return ContinuationProbeResponse(**result)

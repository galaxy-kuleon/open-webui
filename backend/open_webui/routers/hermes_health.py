"""Hermes health-check router.

Exposes a single endpoint:

    GET /api/v1/hermes/health

that probes the Hermes API server and returns a JSON body of the form::

    {"ok": bool, "reason": str}

The HTTP status is always 200 — the health truth lives in the body.
This makes the endpoint trivially consumable from the frontend without
try/catch around HTTP-level errors.

Auth: any verified OWUI user (same precedent as hermes_memory.py).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from open_webui.env import HERMES_API_URL
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()

_HEALTH_TIMEOUT = 3.0  # seconds


class HermesHealthResponse(BaseModel):
    ok: bool
    reason: str = ''


@router.get('', response_model=HermesHealthResponse)
async def hermes_health(user=Depends(get_verified_user)) -> HermesHealthResponse:
    """Probe hermes /health and return {ok, reason}.

    Always returns HTTP 200; health truth is in the JSON body.
    """
    url = f'{HERMES_API_URL.rstrip("/")}/health'

    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.ConnectError as exc:
        log.error('Hermes health probe: cannot connect to %s — %s', url, exc)
        return HermesHealthResponse(ok=False, reason=f'Hermes unreachable at {HERMES_API_URL}')
    except httpx.TimeoutException as exc:
        log.warning('Hermes health probe: timeout after %.1fs — %s', _HEALTH_TIMEOUT, exc)
        return HermesHealthResponse(ok=False, reason='Hermes health check timed out')
    except Exception as exc:
        log.error('Hermes health probe: unexpected error — %s', exc)
        return HermesHealthResponse(ok=False, reason=f'Unexpected error: {exc}')

    if resp.status_code != 200:
        log.warning('Hermes health probe: HTTP %s from %s', resp.status_code, url)
        return HermesHealthResponse(ok=False, reason=f'Hermes returned HTTP {resp.status_code}')

    try:
        body = resp.json()
    except Exception as exc:
        log.warning('Hermes health probe: response is not valid JSON — %s', exc)
        return HermesHealthResponse(ok=False, reason='Hermes health response is not valid JSON')

    if 'status' not in body:
        return HermesHealthResponse(ok=False, reason="Hermes health response missing 'status' key")

    return HermesHealthResponse(ok=True, reason=str(body.get('status', 'ok')))

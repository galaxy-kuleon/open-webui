"""Behavioural tests for GET /api/v1/hermes/continuation/probe (W4 proactive).

Contract under test:
1. When hermes returns ``{suggested: true, task_summary: "...", ...}``, the
   endpoint forwards the payload as-is (plus default-filling missing keys).
2. When hermes returns HTTP 404 (endpoint not yet shipped upstream), the
   OWUI endpoint returns ``{suggested: false}`` — NOT a 5xx.
3. When hermes is unreachable (ConnectError) or times out (TimeoutException),
   the OWUI endpoint returns ``{suggested: false}`` — NOT a 5xx.
4. Identity contract: the ``user_id`` forwarded to hermes is derived
   exclusively from the authenticated ``current_user`` (via
   resolve_hermes_identity), NEVER from any request body field.  Since
   this is a GET endpoint with no body, there is no attack surface, but
   we assert the outgoing hermes payload carries the correct identity.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _FakeUser:
    """Minimal UserModel-shaped object usable by the auth Depends."""

    def __init__(self, user_id: str = 'alice'):
        self.id = user_id
        self.name = 'Alice'
        self.email = f'{user_id}@test.local'
        self.role = 'user'
        self.info = {}
        self.settings = {}
        self.profile_image_url = ''
        self.last_active_at = 0

    def model_dump(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'role': self.role,
        }


def _build_client(user: _FakeUser):
    from fastapi.testclient import TestClient

    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    app.dependency_overrides[get_verified_user] = lambda: user
    return app, TestClient(app)


def _teardown(app):
    from open_webui.utils.auth import get_verified_user

    app.dependency_overrides.pop(get_verified_user, None)


class _MockHermesResponse:
    def __init__(self, json_body: dict, status_code: int = 200):
        self._json = json_body
        self.status_code = status_code

    def json(self) -> dict:
        return self._json


def _mock_hermes_client(response_json: dict, status_code: int = 200):
    mock_resp = _MockHermesResponse(response_json, status_code)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_identity(tenant: str = 'acme'):
    """Return a fake resolve_hermes_identity that uses user.id → user_id."""

    async def _fake(user):
        if user is None:
            return None
        uid = user.get('id') if isinstance(user, dict) else getattr(user, 'id', None)
        if not uid:
            return None
        return {
            'user_id': uid,
            'tenant_id': tenant,
            'display_name': 'Alice',
            'locale': 'en',
        }

    return _fake


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_probe_forwards_suggested_true_from_hermes():
    """Hermes returns suggested=true → endpoint returns the full payload."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    hermes_payload = {
        'suggested': True,
        'task_summary': 'You were porting skip_rag.py to the new ABC.',
        'confidence': 'high',
        'last_session_age_hours': 3.5,
    }
    mock_client = _mock_hermes_client(hermes_payload)

    try:
        with (
            patch('httpx.AsyncClient', return_value=mock_client),
            patch(
                'open_webui.routers.hermes_continuation.resolve_hermes_identity',
                side_effect=_mock_identity('acme'),
            ),
        ):
            resp = client.get('/api/v1/hermes/continuation/probe')
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body['suggested'] is True
            assert body['task_summary'] == 'You were porting skip_rag.py to the new ABC.'
            assert body['confidence'] == 'high'
            assert body['last_session_age_hours'] == pytest.approx(3.5)
    finally:
        _teardown(app)


def test_probe_returns_suggested_false_on_hermes_404():
    """Hermes 404 (upstream endpoint not yet shipped) → {suggested: false}, NOT 5xx."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = _mock_hermes_client({}, status_code=404)

    try:
        with (
            patch('httpx.AsyncClient', return_value=mock_client),
            patch(
                'open_webui.routers.hermes_continuation.resolve_hermes_identity',
                side_effect=_mock_identity(),
            ),
        ):
            resp = client.get('/api/v1/hermes/continuation/probe')
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body['suggested'] is False
            assert body['task_summary'] is None
    finally:
        _teardown(app)


def test_probe_returns_suggested_false_on_hermes_timeout():
    """Hermes timeout → {suggested: false}, NOT 5xx."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException('timed out', request=None))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    try:
        with (
            patch('httpx.AsyncClient', return_value=mock_client),
            patch(
                'open_webui.routers.hermes_continuation.resolve_hermes_identity',
                side_effect=_mock_identity(),
            ),
        ):
            resp = client.get('/api/v1/hermes/continuation/probe')
            assert resp.status_code == 200, resp.text
            assert resp.json()['suggested'] is False
    finally:
        _teardown(app)


def test_probe_returns_suggested_false_on_connect_error():
    """Hermes unreachable → {suggested: false}, NOT 5xx."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError('connection refused'))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    try:
        with (
            patch('httpx.AsyncClient', return_value=mock_client),
            patch(
                'open_webui.routers.hermes_continuation.resolve_hermes_identity',
                side_effect=_mock_identity(),
            ),
        ):
            resp = client.get('/api/v1/hermes/continuation/probe')
            assert resp.status_code == 200, resp.text
            assert resp.json()['suggested'] is False
    finally:
        _teardown(app)


def test_probe_identity_from_current_user_not_request_body():
    """The user_id forwarded to hermes comes from current_user, not the request.

    This is a GET endpoint (no body), so the surface is small, but we
    verify the contract explicitly: hermes receives alice's identity
    (from resolve_hermes_identity) regardless of who is 'logged in'.
    """
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    hermes_payload = {
        'suggested': False,
        'task_summary': None,
        'confidence': 'low',
        'last_session_age_hours': None,
    }
    mock_client = _mock_hermes_client(hermes_payload)

    try:
        with (
            patch('httpx.AsyncClient', return_value=mock_client),
            patch(
                'open_webui.routers.hermes_continuation.resolve_hermes_identity',
                side_effect=_mock_identity('acme-corp'),
            ),
        ):
            resp = client.get('/api/v1/hermes/continuation/probe')
            assert resp.status_code == 200, resp.text

            assert mock_client.post.called, 'hermes was never called'
            call = mock_client.post.call_args
            payload = call.kwargs.get('json') or {}
            # Identity must reflect alice/acme-corp from resolve_hermes_identity
            assert payload.get('user_id') == 'alice'
            assert payload.get('tenant_id') == 'acme-corp'
    finally:
        _teardown(app)

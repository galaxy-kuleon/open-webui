"""Behavioural tests for GET /api/v1/hermes/health.

Contract under test:
- Always returns HTTP 200.
- Health truth is in the JSON body: {"ok": bool, "reason": str}.
- Hermes reachable and returns {"status": "ok"} → ok=true.
- Hermes connection refused → ok=false, reason mentions "unreachable".
- Hermes returns non-200 (e.g. 500) → ok=false, reason mentions HTTP status.
- Hermes returns unparseable JSON → ok=false, reason mentions JSON.
- Hermes returns JSON without "status" key → ok=false, reason mentions "status".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class _FakeUser:
    """Minimal UserModel-shaped object for the auth Depends."""

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
        return {'id': self.id, 'name': self.name, 'email': self.email, 'role': self.role}


def _build_client(user: _FakeUser):
    from fastapi.testclient import TestClient
    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    app.dependency_overrides[get_verified_user] = lambda: user
    return app, TestClient(app)


def _teardown(app):
    from open_webui.utils.auth import get_verified_user

    app.dependency_overrides.pop(get_verified_user, None)


def _mock_httpx_get(json_body: dict | None, status_code: int = 200):
    """Return an async-context-manager mock of httpx.AsyncClient whose .get() resolves."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    if json_body is not None:
        mock_resp.json = MagicMock(return_value=json_body)
    else:
        mock_resp.json = MagicMock(side_effect=ValueError('No JSON object could be decoded'))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_hermes_healthy_returns_ok_true():
    """Hermes responds 200 with {"status": "ok"} → body.ok is True."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_httpx = _mock_httpx_get({'status': 'ok'}, status_code=200)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['ok'] is True
        assert body['reason'] == 'ok'
    finally:
        _teardown(app)


def test_hermes_healthy_preserves_status_string():
    """reason echoes whatever the status field contains."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_httpx = _mock_httpx_get({'status': 'healthy', 'version': '1.2.3'}, status_code=200)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        assert resp.json()['ok'] is True
        assert resp.json()['reason'] == 'healthy'
    finally:
        _teardown(app)


def test_hermes_connection_refused_returns_ok_false_unreachable():
    """ConnectError → ok=False, reason mentions 'unreachable'."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError('connection refused'))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    try:
        with patch('httpx.AsyncClient', return_value=mock_client):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert 'unreachable' in body['reason'].lower(), f"Expected 'unreachable' in reason: {body['reason']}"
    finally:
        _teardown(app)


def test_hermes_timeout_returns_ok_false():
    """TimeoutException → ok=False, reason mentions timeout."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException('timeout'))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    try:
        with patch('httpx.AsyncClient', return_value=mock_client):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert any(t in body['reason'].lower() for t in ('timeout', 'timed')), (
            f"Expected 'timeout' or 'timed' in reason: {body['reason']}"
        )
    finally:
        _teardown(app)


def test_hermes_500_returns_ok_false_with_http_status():
    """Hermes HTTP 500 → ok=False, reason mentions HTTP status code."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_httpx = _mock_httpx_get({'error': 'internal'}, status_code=500)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert '500' in body['reason'], f"Expected '500' in reason: {body['reason']}"
    finally:
        _teardown(app)


def test_hermes_non_200_returns_ok_false():
    """Any non-200 response → ok=False, reason mentions HTTP status."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_httpx = _mock_httpx_get({'error': 'bad gateway'}, status_code=502)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert '502' in body['reason'], f"Expected '502' in reason: {body['reason']}"
    finally:
        _teardown(app)


def test_hermes_garbage_json_returns_ok_false():
    """Hermes returns 200 but non-JSON body → ok=False, reason mentions JSON."""
    user = _FakeUser()
    app, client = _build_client(user)

    # None json_body triggers ValueError from mock_resp.json()
    mock_httpx = _mock_httpx_get(None, status_code=200)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert 'json' in body['reason'].lower(), f"Expected 'json' in reason: {body['reason']}"
    finally:
        _teardown(app)


def test_hermes_missing_status_key_returns_ok_false():
    """Hermes returns 200 + valid JSON but no 'status' key → ok=False, reason mentions 'status'."""
    user = _FakeUser()
    app, client = _build_client(user)

    mock_httpx = _mock_httpx_get({'uptime': 12345}, status_code=200)

    try:
        with patch('httpx.AsyncClient', return_value=mock_httpx):
            resp = client.get('/api/v1/hermes/health')
        assert resp.status_code == 200
        body = resp.json()
        assert body['ok'] is False
        assert 'status' in body['reason'].lower(), f"Expected 'status' in reason: {body['reason']}"
    finally:
        _teardown(app)


def test_unauthenticated_request_is_rejected():
    """Without overriding auth, the endpoint should reject unauthenticated requests."""
    from fastapi.testclient import TestClient
    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    # Remove any override so real auth kicks in
    app.dependency_overrides.pop(get_verified_user, None)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get('/api/v1/hermes/health')
    # Unauthenticated → 401 or 403
    assert resp.status_code in (401, 403), f'Expected 401/403, got {resp.status_code}'

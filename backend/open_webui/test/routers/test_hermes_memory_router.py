"""Behavioural tests for /api/v1/hermes/memory/* router (W3 T1).

Contract under test:
1. Every endpoint calls hermes /v1/memory/tool with the authenticated
   user's identity (resolve_hermes_identity(current_user)), NOT any
   identity field that might appear in the request body. This is the
   IDOR defence — a user cannot craft a request to read another user's
   memory by passing a different user_id in the body.
2. Unauthenticated requests are rejected before any hermes call.
3. Hermes unreachable / timeout / error responses surface as 5xx from
   the router (not as opaque 500s).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _FakeUser:
    """Minimal UserModel-shaped object for the auth Depends + identity path."""

    def __init__(self, user_id: str = 'alice', name: str = 'Alice'):
        self.id = user_id
        self.name = name
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
    """Build a TestClient with auth dependency overridden to return `user`."""
    from fastapi.testclient import TestClient
    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    app.dependency_overrides[get_verified_user] = lambda: user
    client = TestClient(app)
    return app, client


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
    """Patch httpx.AsyncClient used inside the router."""
    mock_resp = _MockHermesResponse(response_json, status_code)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_identity_lookup(tenant: str = 'acme'):
    """Patch resolve_hermes_identity so no DB hit."""
    def _fake(user):
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


def test_get_profile_calls_hermes_with_current_user_identity():
    """GET /profile → hermes POST /v1/memory/tool carries current_user's id, not body's."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = _mock_hermes_client({'result': {'facts': []}})
    mock_identity = _mock_identity_lookup(tenant='acme-corp')

    try:
        with patch('httpx.AsyncClient', return_value=mock_client), \
             patch('open_webui.routers.hermes_memory.resolve_hermes_identity', side_effect=mock_identity):
            resp = client.get('/api/v1/hermes/memory/profile')
            assert resp.status_code == 200, resp.text

            # Verify hermes was called with alice's identity
            assert mock_client.post.called
            call = mock_client.post.call_args
            payload = call.kwargs.get('json') or (call.args[1] if len(call.args) > 1 else {})
            assert payload['tool_name'] == 'fact_store'
            assert payload['args']['action'] == 'list'
            assert payload['user_id'] == 'alice'
            assert payload['tenant_id'] == 'acme-corp'
    finally:
        _teardown(app)


def test_post_profile_ignores_body_user_id_idor_defence():
    """POST /profile body cannot override current_user identity (IDOR)."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = _mock_hermes_client({'result': {'fact_id': 7, 'status': 'added'}})
    mock_identity = _mock_identity_lookup(tenant='acme-corp')

    try:
        with patch('httpx.AsyncClient', return_value=mock_client), \
             patch('open_webui.routers.hermes_memory.resolve_hermes_identity', side_effect=mock_identity):
            # Attempt to spoof user_id via a parameter the router does NOT honour.
            # Our router's AddFactRequest model does not declare user_id, so even
            # if the body carries one, pydantic strips it. Additional defence
            # would be redundant — but we assert the outgoing hermes payload
            # uses alice regardless of what was in the body.
            resp = client.post(
                '/api/v1/hermes/memory/profile',
                json={
                    'content': 'my favourite framework is Playwright',
                    'category': 'user_pref',
                    # Attempted spoof — MUST be ignored
                    'user_id': 'mallory',
                    'tenant_id': 'evil-corp',
                },
            )
            assert resp.status_code == 200, resp.text

            call = mock_client.post.call_args
            payload = call.kwargs.get('json') or (call.args[1] if len(call.args) > 1 else {})
            # Authoritative identity is alice/acme-corp, NOT the body's spoof.
            assert payload['user_id'] == 'alice'
            assert payload['tenant_id'] == 'acme-corp'
            assert payload['args']['action'] == 'add'
            assert payload['args']['content'] == 'my favourite framework is Playwright'
    finally:
        _teardown(app)


def test_delete_profile_fact_scoped_by_current_user():
    """DELETE /profile/<id> scopes by current_user; fact_id flows through."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = _mock_hermes_client({'result': {'removed': True}})
    mock_identity = _mock_identity_lookup(tenant='acme-corp')

    try:
        with patch('httpx.AsyncClient', return_value=mock_client), \
             patch('open_webui.routers.hermes_memory.resolve_hermes_identity', side_effect=mock_identity):
            resp = client.delete('/api/v1/hermes/memory/profile/42')
            assert resp.status_code == 200, resp.text

            call = mock_client.post.call_args
            payload = call.kwargs.get('json') or (call.args[1] if len(call.args) > 1 else {})
            assert payload['tool_name'] == 'fact_store'
            assert payload['args']['action'] == 'remove'
            assert payload['args']['fact_id'] == 42
            assert payload['user_id'] == 'alice'
            assert payload['tenant_id'] == 'acme-corp'
    finally:
        _teardown(app)


def test_search_query_param_passed_through():
    """GET /search?q=... forwards query into hermes fact_store.search."""
    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_client = _mock_hermes_client({'result': {'results': [], 'count': 0}})
    mock_identity = _mock_identity_lookup()

    try:
        with patch('httpx.AsyncClient', return_value=mock_client), \
             patch('open_webui.routers.hermes_memory.resolve_hermes_identity', side_effect=mock_identity):
            resp = client.get('/api/v1/hermes/memory/search?q=playwright&limit=5')
            assert resp.status_code == 200, resp.text

            call = mock_client.post.call_args
            payload = call.kwargs.get('json') or (call.args[1] if len(call.args) > 1 else {})
            assert payload['args']['action'] == 'search'
            assert payload['args']['query'] == 'playwright'
            assert payload['args']['limit'] == 5
    finally:
        _teardown(app)


def test_hermes_unreachable_returns_502():
    """When hermes connection fails, router responds 502 with a clear detail."""
    import httpx

    user = _FakeUser(user_id='alice')
    app, client = _build_client(user)

    mock_identity = _mock_identity_lookup()

    # AsyncClient that raises ConnectError on post
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError('cannot connect'))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    try:
        with patch('httpx.AsyncClient', return_value=mock_client), \
             patch('open_webui.routers.hermes_memory.resolve_hermes_identity', side_effect=mock_identity):
            resp = client.get('/api/v1/hermes/memory/profile')
            assert resp.status_code == 502
            assert 'unreachable' in resp.json().get('detail', '').lower()
    finally:
        _teardown(app)

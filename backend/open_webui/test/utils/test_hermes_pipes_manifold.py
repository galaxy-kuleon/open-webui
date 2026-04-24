"""Tests for Pipe.pipes() — manifold discovery method.

Covers 2 scenarios:
  (a) Hermes returns a model list → pipes() returns one entry per model
  (b) Hermes is unreachable (ConnectError) → pipes() returns []

The existing test_hermes_pipe.py tests the SSE streaming path; this file
covers the GET /v1/models discovery path used to populate the model dropdown.
"""

import pytest
import httpx

from open_webui.pipes.hermes_agent import Pipe


# ---------------------------------------------------------------------------
# Fake httpx client for GET requests (non-streaming)
# ---------------------------------------------------------------------------

class _FakeGetResponse:
    """Minimal stand-in for httpx.Response from client.get()."""

    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class _FakeGetClient:
    """Async context manager that returns a _FakeGetResponse for GET calls."""

    def __init__(self, response: _FakeGetResponse | None = None, raise_on_get=None):
        self._response = response
        self._raise_on_get = raise_on_get

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return self._response


def _get_client_factory(response=None, raise_on_get=None):
    """Return a class that can be instantiated as httpx.AsyncClient(timeout=...)."""
    _response = response
    _raise = raise_on_get

    class _Factory:
        def __init__(self, *args, **kwargs):
            self._client = _FakeGetClient(response=_response, raise_on_get=_raise)

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return _Factory


# ---------------------------------------------------------------------------
# Scenario (a): Hermes reachable → model list returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipes_returns_formatted_model_list(monkeypatch):
    """When hermes /v1/models returns a list of profiles, pipes() must return
    one dict per model with 'id' and 'name' keys set from the model id."""
    models_body = {
        'data': [
            {'id': 'default'},
            {'id': 'research'},
            {'id': 'coder'},
        ]
    }
    fake_response = _FakeGetResponse(status_code=200, body=models_body)
    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _get_client_factory(response=fake_response),
    )

    pipe = Pipe()
    result = await pipe.pipes()

    _meta = {'capabilities': {'delegated_orchestration': True}}
    assert result == [
        {'id': 'default', 'name': 'default', 'meta': _meta},
        {'id': 'research', 'name': 'research', 'meta': _meta},
        {'id': 'coder', 'name': 'coder', 'meta': _meta},
    ]


@pytest.mark.asyncio
async def test_pipes_empty_data_returns_default_entry(monkeypatch):
    """When hermes /v1/models returns an empty data list, pipes() must fall back
    to a single {'id': 'default', 'name': 'Hermes Agent'} entry."""
    fake_response = _FakeGetResponse(status_code=200, body={'data': []})
    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _get_client_factory(response=fake_response),
    )

    pipe = Pipe()
    result = await pipe.pipes()

    _meta = {'capabilities': {'delegated_orchestration': True}}
    assert result == [{'id': 'default', 'name': 'Hermes Agent', 'meta': _meta}]


# ---------------------------------------------------------------------------
# Scenario (b): Hermes unreachable (ConnectError) → empty list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pipes_connect_error_returns_empty_list(monkeypatch):
    """When httpx raises ConnectError (hermes not running), pipes() must
    swallow the exception and return [] so the model dropdown stays empty."""
    connect_error = httpx.ConnectError('Connection refused')
    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _get_client_factory(raise_on_get=connect_error),
    )

    pipe = Pipe()
    result = await pipe.pipes()

    assert result == []

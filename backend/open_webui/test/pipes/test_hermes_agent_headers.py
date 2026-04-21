"""Tests that hermes_agent.pipe() injects identity headers correctly.

Mocks both Groups.get_groups_by_member_id (DB) and httpx.AsyncClient (network)
so no external I/O occurs and the SSE loop terminates cleanly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — fake httpx SSE stream that ends immediately with [DONE]
# ---------------------------------------------------------------------------


def _make_fake_httpx_client(captured_headers: dict):
    """Return a mock httpx.AsyncClient whose .stream() call records headers.

    The fake stream yields a single ``data: [DONE]`` line so the pipe's
    SSE loop terminates without blocking.
    """

    async def _fake_aiter_lines():
        yield 'data: [DONE]'

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.aiter_lines = _fake_aiter_lines

    # ``async with client.stream(...) as response`` → async context manager
    fake_stream_cm = MagicMock()
    fake_stream_cm.__aenter__ = AsyncMock(return_value=fake_response)
    fake_stream_cm.__aexit__ = AsyncMock(return_value=False)

    def _stream_side_effect(method, url, headers=None, json=None, **kwargs):
        # Capture the headers dict the pipe actually passed
        if headers is not None:
            captured_headers.update(headers)
        return fake_stream_cm

    fake_client = MagicMock()
    fake_client.stream = MagicMock(side_effect=_stream_side_effect)

    # ``async with httpx.AsyncClient(...) as client`` → async context manager
    fake_client_cm = MagicMock()
    fake_client_cm.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client_cm.__aexit__ = AsyncMock(return_value=False)

    return fake_client_cm


def _make_group(group_id: str, tenancy: str | None = None) -> SimpleNamespace:
    meta = {'tenancy': tenancy} if tenancy is not None else None
    return SimpleNamespace(id=group_id, meta=meta)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_headers_present_with_authenticated_user():
    """When __user__ is provided, both identity headers must reach httpx.stream."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    captured: dict = {}
    body = {'messages': [{'role': 'user', 'content': 'hello'}], 'model': 'hermes'}

    fake_groups = [_make_group('g-acme', tenancy='acme-corp')]

    with (
        patch(
            'open_webui.hermes.identity.Groups.get_groups_by_member_id',
            return_value=fake_groups,
        ),
        patch(
            'httpx.AsyncClient',
            return_value=_make_fake_httpx_client(captured),
        ),
    ):
        # Drain the async generator
        chunks = []
        async for chunk in pipe.pipe(
            body=body,
            __user__={'id': 'alice', 'name': 'Alice A', 'email': 'alice@x.com', 'role': 'user'},
            __event_emitter__=None,
        ):
            chunks.append(chunk)

    assert 'X-Hermes-User-Id' in captured, 'X-Hermes-User-Id header must be present when __user__ is set'
    assert 'X-Hermes-Tenant-Id' in captured, 'X-Hermes-Tenant-Id header must be present when __user__ is set'
    assert captured['X-Hermes-User-Id'] == 'alice'
    assert captured['X-Hermes-Tenant-Id'] == 'acme-corp'


@pytest.mark.asyncio
async def test_headers_absent_when_user_is_none():
    """When __user__ is None, neither identity header must be present."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    captured: dict = {}
    body = {'messages': [{'role': 'user', 'content': 'hello'}], 'model': 'hermes'}

    with patch(
        'httpx.AsyncClient',
        return_value=_make_fake_httpx_client(captured),
    ):
        chunks = []
        async for chunk in pipe.pipe(
            body=body,
            __user__=None,
            __event_emitter__=None,
        ):
            chunks.append(chunk)

    assert 'X-Hermes-User-Id' not in captured, 'X-Hermes-User-Id must NOT be sent when __user__ is None'
    assert 'X-Hermes-Tenant-Id' not in captured, 'X-Hermes-Tenant-Id must NOT be sent when __user__ is None'


@pytest.mark.asyncio
async def test_headers_use_user_id_as_tenant_when_no_groups():
    """User with no groups: tenant_id falls back to user_id (tier 3)."""
    from open_webui.pipes.hermes_agent import Pipe

    pipe = Pipe()
    captured: dict = {}
    body = {'messages': [{'role': 'user', 'content': 'ping'}], 'model': 'hermes'}

    with (
        patch(
            'open_webui.hermes.identity.Groups.get_groups_by_member_id',
            return_value=[],
        ),
        patch(
            'httpx.AsyncClient',
            return_value=_make_fake_httpx_client(captured),
        ),
    ):
        async for _ in pipe.pipe(
            body=body,
            __user__={'id': 'solo-user', 'name': 'Solo', 'email': 's@x.com', 'role': 'user'},
            __event_emitter__=None,
        ):
            pass

    assert captured.get('X-Hermes-User-Id') == 'solo-user'
    assert captured.get('X-Hermes-Tenant-Id') == 'solo-user'

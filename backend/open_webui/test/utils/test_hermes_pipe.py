import json

import pytest

from open_webui.pipes.hermes_agent import Pipe


class _FakeResponse:
    def __init__(self, status_code=200, lines=None, json_body=None):
        self.status_code = status_code
        self._lines = lines or ['data: [DONE]']
        self._json_body = json_body or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return json.dumps(self._json_body).encode()

    def json(self):
        return self._json_body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, recorder, response):
        self._recorder = recorder
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers=None, json=None):
        self._recorder['request_kind'] = 'stream'
        self._recorder['method'] = method
        self._recorder['url'] = url
        self._recorder['headers'] = headers or {}
        self._recorder['json'] = json or {}
        return self._response

    async def post(self, url, headers=None, json=None):
        self._recorder['request_kind'] = 'post'
        self._recorder['method'] = 'POST'
        self._recorder['url'] = url
        self._recorder['headers'] = headers or {}
        self._recorder['json'] = json or {}
        return self._response


def _client_factory(recorder, response):
    class _Factory:
        def __init__(self, *args, **kwargs):
            self._client = _FakeClient(recorder, response)

        async def __aenter__(self):
            return await self._client.__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            return await self._client.__aexit__(exc_type, exc, tb)

        def stream(self, method, url, headers=None, json=None):
            return self._client.stream(method, url, headers=headers, json=json)

        async def post(self, url, headers=None, json=None):
            return await self._client.post(url, headers=headers, json=json)

    return _Factory


def test_maybe_add_session_header_requires_api_key():
    pipe = Pipe()
    pipe.valves.hermes_api_key = ''
    headers = {}

    assert pipe._maybe_add_session_header(headers, 'chat-123') is False
    assert 'X-Hermes-Session-Id' not in headers

    pipe.valves.hermes_api_key = 'sk-test'
    assert pipe._maybe_add_session_header(headers, 'chat-123') is True
    assert headers['X-Hermes-Session-Id'] == 'chat-123'


@pytest.mark.asyncio
async def test_pipe_skips_session_header_without_api_key(monkeypatch):
    pipe = Pipe()
    pipe.valves.hermes_api_key = ''
    recorder = {}
    response = _FakeResponse()

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    stream = await pipe.pipe(
        {'model': 'hermes_agent.default', 'messages': [{'role': 'user', 'content': 'hello'}]},
        __chat_id__='chat-123',
    )

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)

    assert chunks == ['data: [DONE]']
    assert 'X-Hermes-Session-Id' not in recorder['headers']


@pytest.mark.asyncio
async def test_pipe_adds_session_header_with_api_key(monkeypatch):
    pipe = Pipe()
    pipe.valves.hermes_api_key = 'sk-test'
    recorder = {}
    response = _FakeResponse()

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    stream = await pipe.pipe(
        {'model': 'hermes_agent.default', 'messages': [{'role': 'user', 'content': 'hello'}]},
        __chat_id__='chat-123',
    )

    async for _ in stream:
        pass

    assert recorder['headers']['Authorization'] == 'Bearer sk-test'
    assert recorder['headers']['X-Hermes-Session-Id'] == 'chat-123'


@pytest.mark.asyncio
async def test_pipe_403_session_continuity_error_includes_hint(monkeypatch):
    pipe = Pipe()
    pipe.valves.hermes_api_key = 'sk-test'
    recorder = {}
    response = _FakeResponse(
        status_code=403,
        json_body={'error': {'message': 'Session continuation requires API key authentication.'}},
    )

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    emitted = []

    async def _emit(event):
        emitted.append(event)

    stream = await pipe.pipe(
        {'model': 'hermes_agent.default', 'messages': [{'role': 'user', 'content': 'hello'}]},
        __chat_id__='chat-123',
        __event_emitter__=_emit,
    )

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)

    assert chunks[0]['error']['detail'].endswith(
        'Hermes session continuity requires API_SERVER_KEY on the Hermes server and a matching '
        'hermes_api_key in this pipe.'
    )
    assert emitted[-1]['data']['sub_action'] == 'error'


@pytest.mark.asyncio
async def test_pipe_passes_through_reasoning_content_unchanged(monkeypatch):
    """Regression: reasoning_content in a delta must pass through unmodified.

    The dead branch at hermes_agent.py:212-220 used to pop reasoning_content /
    reasoning from the delta and emit a 'thinking' status.  After its removal
    the chunk must be yielded with all fields intact and no 'thinking' status
    event must be emitted.
    """
    pipe = Pipe()
    recorder = {}
    response = _FakeResponse(
        lines=[
            'data: {"choices":[{"delta":{"reasoning_content":"thinking out loud","content":"hi"}}]}',
            'data: [DONE]',
        ]
    )

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    emitted = []

    async def _emit(event):
        emitted.append(event)

    stream = await pipe.pipe(
        {'model': 'hermes_agent.default', 'messages': [{'role': 'user', 'content': 'hello'}]},
        __event_emitter__=_emit,
    )

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)

    # First yielded chunk must be the parsed JSON with reasoning_content intact
    expected_chunk = {'choices': [{'delta': {'reasoning_content': 'thinking out loud', 'content': 'hi'}}]}
    assert chunks[0] == expected_chunk

    # No emitter event should carry sub_action == 'thinking'
    thinking_events = [e for e in emitted if e.get('data', {}).get('sub_action') == 'thinking']
    assert thinking_events == []


@pytest.mark.asyncio
async def test_pipe_returns_json_for_non_stream_requests(monkeypatch):
    pipe = Pipe()
    recorder = {}
    response = _FakeResponse(
        json_body={
            'id': 'chatcmpl-test',
            'object': 'chat.completion',
            'choices': [
                {
                    'index': 0,
                    'message': {'role': 'assistant', 'content': 'hello back'},
                    'finish_reason': 'stop',
                }
            ],
        }
    )

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    result = await pipe.pipe(
        {
            'model': 'hermes_agent.default',
            'messages': [{'role': 'user', 'content': 'hello'}],
            'stream': False,
        }
    )

    assert result == response.json()
    assert recorder['request_kind'] == 'post'
    assert recorder['json']['stream'] is False


@pytest.mark.asyncio
async def test_pipe_returns_error_dict_for_non_stream_failures(monkeypatch):
    pipe = Pipe()
    pipe.valves.hermes_api_key = 'sk-test'
    recorder = {}
    response = _FakeResponse(
        status_code=403,
        json_body={'error': {'message': 'Session continuation requires API key authentication.'}},
    )

    monkeypatch.setattr(
        'open_webui.pipes.hermes_agent.httpx.AsyncClient',
        _client_factory(recorder, response),
    )

    emitted = []

    async def _emit(event):
        emitted.append(event)

    result = await pipe.pipe(
        {
            'model': 'hermes_agent.default',
            'messages': [{'role': 'user', 'content': 'hello'}],
            'stream': False,
        },
        __chat_id__='chat-123',
        __event_emitter__=_emit,
    )

    assert result['error']['detail'].endswith(
        'Hermes session continuity requires API_SERVER_KEY on the Hermes server and a matching '
        'hermes_api_key in this pipe.'
    )
    assert recorder['request_kind'] == 'post'
    assert emitted[-1]['data']['sub_action'] == 'error'

from types import SimpleNamespace

import pytest

from open_webui.utils import middleware


@pytest.mark.asyncio
async def test_explicit_current_file_conversion_reaches_real_tool_callable(monkeypatch):
    calls = []
    events = []

    async def tool_callable(**parameters):
        calls.append(parameters)
        return {'ok': True, 'reply_markdown': 'queued'}

    async def process_tool_result(*_args, **_kwargs):
        return '{"ok":true,"reply_markdown":"queued"}', [], []

    async def emit(event):
        events.append(event)

    monkeypatch.setattr(middleware, 'process_tool_result', process_tool_result)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TASK_MODEL='',
                    TASK_MODEL_EXTERNAL='',
                    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE='',
                )
            )
        )
    )
    body = {
        'model': 'origin-pro',
        'messages': [
            {
                'role': 'user',
                'content': 'using mcp_soc_v2_submit_conversion: translate to Chinese',
            }
        ],
        'metadata': {'files': []},
    }
    metadata = {
        'chat_id': 'chat-1',
        'message_id': 'message-1',
        'files': [{'type': 'file', 'id': 'file-123'}],
    }
    tools = {
        'socv2_submit_conversion': {
            'spec': {
                'name': 'socv2_submit_conversion',
                'parameters': {
                    'properties': {
                        'file_id': {},
                        'strict_file_id': {},
                        'translate': {},
                        'target_lang': {},
                    }
                },
            },
            'callable': tool_callable,
            'type': 'mcp',
            'direct': False,
        },
        'socv2_resubmit_conversion': {
            'spec': {'name': 'socv2_resubmit_conversion', 'parameters': {'properties': {}}},
            'callable': tool_callable,
            'type': 'mcp',
            'direct': False,
        },
    }

    _, flags = await middleware.chat_completion_tools_handler(
        request,
        body,
        {
            '__event_call__': emit,
            '__event_emitter__': emit,
            '__metadata__': metadata,
        },
        SimpleNamespace(id='user-1'),
        {'origin-pro': {'id': 'origin-pro'}},
        tools,
    )

    assert calls == [
        {
            'file_id': 'file-123',
            'strict_file_id': True,
            'translate': True,
            'target_lang': 'zh-tw',
        }
    ]
    assert flags['sources'][0]['source']['name'] == 'socv2_submit_conversion'
    assert flags['sources'][0]['tool_result'] is True

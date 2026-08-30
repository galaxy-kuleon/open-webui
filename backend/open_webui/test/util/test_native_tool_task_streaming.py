from open_webui.utils.middleware import ensure_native_tool_task_streaming


def _metadata(**overrides):
    value = {
        'session_id': 'session-1',
        'chat_id': 'chat-1',
        'message_id': 'message-1',
        'params': {'function_calling': 'native'},
        'tools': {'query_knowledge_files': {}},
    }
    value.update(overrides)
    return value


def test_stored_native_tool_task_is_promoted_without_mutating_admission_payload():
    admitted = {'model': 'origin-flash', 'stream': False}

    execution, promoted = ensure_native_tool_task_streaming(admitted, _metadata())

    assert promoted is True
    assert execution == {'model': 'origin-flash', 'stream': True}
    assert admitted == {'model': 'origin-flash', 'stream': False}


def test_already_streaming_native_tool_task_is_unchanged():
    admitted = {'model': 'origin-flash', 'stream': True}

    execution, promoted = ensure_native_tool_task_streaming(admitted, _metadata())

    assert promoted is False
    assert execution is admitted


def test_inline_native_tool_api_request_keeps_non_streaming_contract():
    admitted = {'model': 'origin-flash', 'stream': False}

    execution, promoted = ensure_native_tool_task_streaming(
        admitted,
        _metadata(session_id=None),
    )

    assert promoted is False
    assert execution is admitted


def test_stored_chat_without_native_tools_keeps_requested_mode():
    admitted = {'model': 'origin-pro', 'stream': False}

    execution, promoted = ensure_native_tool_task_streaming(
        admitted,
        _metadata(tools={}),
    )

    assert promoted is False
    assert execution is admitted

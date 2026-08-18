"""Behavioural contract for clean non-streaming completions with no answer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from open_webui.utils import failure_surface, middleware


class _Chats:
    def __init__(self, results=(), *, selected_raises=False):
        self.results = list(results)
        self.selected_raises = selected_raises
        self.calls = []

    async def upsert_message_to_chat_by_id_and_message_id(
        self, chat_id, message_id, message_patch
    ):
        self.calls.append((chat_id, message_id, message_patch))
        if self.selected_raises and 'selectedModelId' in message_patch:
            raise RuntimeError('synthetic selected-model write failure')
        result = self.results.pop(0) if self.results else {'id': chat_id}
        if isinstance(result, BaseException):
            raise result
        return result


async def _drive_async(
    response_data,
    *,
    chat_id='chat-12345678',
    chats_results=(),
    emitter_raises=False,
    selected_raises=False,
):
    chats = _Chats(chats_results, selected_raises=selected_raises)
    events = []
    markers = []

    async def event_emitter(event):
        events.append(event)
        if emitter_raises:
            raise RuntimeError('synthetic event sink failure')

    def marker_spy(*args, **kwargs):
        markers.append((args, kwargs))

    ctx = {
        'request': SimpleNamespace(),
        'user': SimpleNamespace(),
        'metadata': {'chat_id': chat_id, 'message_id': 'msg-12345678'},
        'events': [],
        'event_emitter': event_emitter,
    }
    with (
        patch.object(middleware, 'Chats', chats),
        patch.object(middleware, 'log_empty_turn', marker_spy),
    ):
        result = await middleware.non_streaming_chat_response_handler(
            response_data, ctx
        )
    return result, chats, events, markers


def _drive(*args, **kwargs):
    return asyncio.run(_drive_async(*args, **kwargs))


def _terminal_call(chats):
    return next(
        (call for call in chats.calls if 'error' in call[2]),
        None,
    )


@pytest.mark.parametrize(
    'response_data',
    [
        {'choices': []},
        {'error': None, 'choices': [{'message': {'content': ''}}]},
        {'choices': [{'message': {}}]},
    ],
)
def test_a_clean_empty_response_terminalizes_with_the_existing_cause(
    response_data,
):
    result, chats, events, markers = _drive(response_data)

    terminal = _terminal_call(chats)
    assert terminal is not None, 'the stored placeholder was left unfinished'
    assert terminal[2]['done'] is True
    assert terminal[2]['error']['cause'] == failure_surface.CAUSE_EMPTY_FINALIZED
    assert result == response_data
    assert [event['type'] for event in events] == ['chat:message:error']
    assert len(markers) == 1
    assert markers[0][0][1] == failure_surface.PHASE_FINALIZED
    assert markers[0][0][4] == failure_surface.NOTICE_WRITTEN


@pytest.mark.parametrize('write_result', [None, RuntimeError('synthetic write failure')])
def test_an_unconfirmed_write_cannot_consume_the_best_effort_event(
    write_result,
):
    response = {'choices': []}
    result, chats, events, markers = _drive(
        response, chats_results=[write_result]
    )

    assert _terminal_call(chats) is not None, 'the terminal write was not attempted'
    assert result == response
    assert [event['type'] for event in events] == ['chat:message:error']
    assert len(markers) == 1
    assert markers[0][0][4] == failure_surface.NOTICE_UNDELIVERED


def test_event_acceptance_is_not_assumed_when_the_sink_raises():
    response = {'choices': []}
    result, chats, events, markers = _drive(
        response, emitter_raises=True
    )

    assert _terminal_call(chats) is not None
    assert result == response, 'a best-effort event changed caller behaviour'
    assert [event['type'] for event in events] == ['chat:message:error']
    assert markers[0][0][4] == failure_surface.NOTICE_UNDELIVERED


def test_channel_scope_is_not_given_a_stored_turn_banner():
    response = {'choices': []}
    result, chats, events, markers = _drive(
        response, chat_id='channel:synthetic'
    )

    assert result == response
    assert chats.calls == []
    assert events == []
    assert markers == []


def test_selected_model_bookkeeping_cannot_preempt_terminalization():
    response = {'selected_model_id': 'synthetic-model', 'choices': []}
    result, chats, events, markers = _drive(
        response, selected_raises=True
    )

    assert _terminal_call(chats) is not None
    assert any('selectedModelId' in call[2] for call in chats.calls)
    assert result == response
    assert [event['type'] for event in events] == ['chat:message:error']
    assert markers[0][0][4] == failure_surface.NOTICE_WRITTEN

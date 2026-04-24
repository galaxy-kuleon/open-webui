"""
W5-T1 Behavioral Tests: Tier-3 cascade failure modes (F-10 / H-2).

Tests verify:
1. Happy path: all extractions succeed, budget met — sources returned unchanged.
2. All-fail: all tasks raise exception → token_cascade_failed event + [] return.
3. Partial-fail: 3 of 5 fail → token_cascade_partial event with correct map + only successful docs.
4. All succeed but still over budget → token_cascade_failed event + [] return.
5. Sentinel contract: extract_relevant_content_from_document returns None (not original) on exception.
"""

import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from open_webui.utils.middleware import (
    apply_token_budget_cascade,
    extract_relevant_content_from_document,
    estimate_sources_total_tokens,
)


# ── Helpers ──────────────────────────────────────────────────────


def make_source(doc_text='hello world', file_id='f1', name='test.md'):
    """Minimal source dict matching the cascade shape."""
    return {
        'source': {'id': file_id, 'name': name, 'type': 'file'},
        'document': [doc_text],
        'metadata': [{'file_id': file_id, 'name': name, 'source': name}],
    }


def _mock_request(concurrency=3):
    req = MagicMock()
    req.app.state.config.RAG_SUBCHAT_CONCURRENCY = concurrency
    return req


def _event_calls(mock_emitter):
    """Return the list of dicts passed to the event emitter."""
    return [c.args[0] for c in mock_emitter.call_args_list]


def _actions(mock_emitter):
    return [ev['data']['action'] for ev in _event_calls(mock_emitter)]


# ── Test 1: Happy path — all succeed, budget met ──────────────────


@pytest.mark.asyncio
async def test_tier3_all_succeed():
    """
    When all extractions succeed and total tokens fit the budget,
    the cascade returns the extracted sources without emitting failure events.
    """
    # Budget large enough that extracted results fit
    source = make_source('x ' * 500)  # ~500 tokens
    event_emitter = AsyncMock()

    with (
        patch('open_webui.utils.middleware.Files') as MockFiles,
        patch('open_webui.utils.middleware.extract_relevant_content_from_document') as mock_extract,
        patch('open_webui.utils.middleware.estimate_sources_total_tokens') as mock_tokens,
    ):
        # Tier 1 over budget (> max_tokens), Tier 2 unavailable, Tier 3 within budget
        # estimate_sources_total_tokens called 3 times: Tier1, Tier3-post-gather
        # We need Tier1 > max, Tier3-post < max
        mock_tokens.side_effect = [
            9999,  # Tier 1: over budget → proceed past Tier 1
            5,  # Tier 3 post-gather: within budget → return extracted
        ]
        MockFiles.get_file_by_id.return_value = None  # No index → Tier 2 fails
        mock_extract.return_value = 'extracted content'

        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=100,
            request=_mock_request(),
            body={'model': 'test', 'messages': [{'role': 'user', 'content': 'query'}]},
            user=MagicMock(),
            event_emitter=event_emitter,
        )

    # Should return extracted sources
    assert len(result) == 1
    assert result[0]['document'] == ['extracted content']

    # No failure events should have been emitted
    actions = _actions(event_emitter)
    assert 'token_cascade_failed' not in actions, f'Unexpected token_cascade_failed: {actions}'
    assert 'token_cascade_partial' not in actions, f'Unexpected token_cascade_partial: {actions}'


# ── Test 2: All tasks raise exception → failed event + [] ─────────


@pytest.mark.asyncio
async def test_tier3_all_fail_returns_empty_with_event():
    """
    When ALL extraction tasks raise an exception:
    - The cascade emits a token_cascade_failed status event.
    - Returns [] (empty list).
    """
    source = make_source('x ' * 500)
    event_emitter = AsyncMock()

    with (
        patch('open_webui.utils.middleware.Files') as MockFiles,
        patch('open_webui.utils.middleware.extract_relevant_content_from_document') as mock_extract,
        patch('open_webui.utils.middleware.estimate_sources_total_tokens') as mock_tokens,
    ):
        # Tier 1: over budget; Tier 2: unavailable; Tier 3: all raise, still over budget
        mock_tokens.side_effect = [
            9999,  # Tier 1: over budget
            9999,  # Tier 3 post-gather: still over budget (unchanged sources)
        ]
        MockFiles.get_file_by_id.return_value = None
        # The _extract inner task returns a tuple, and extract_relevant raises.
        # But _extract catches the raise from extract_relevant because asyncio.gather
        # with return_exceptions=True catches the exception from the coroutine.
        # The _extract wrapper itself raises when extract_relevant raises inside the semaphore.
        mock_extract.side_effect = RuntimeError('LLM timeout')

        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=100,
            request=_mock_request(),
            body={'model': 'test', 'messages': [{'role': 'user', 'content': 'query'}]},
            user=MagicMock(),
            event_emitter=event_emitter,
        )

    assert result == [], f'Expected [] on all-fail, got {result}'
    actions = _actions(event_emitter)
    assert 'token_cascade_failed' in actions, f'Expected token_cascade_failed event, got: {actions}'


# ── Test 3: Partial failure — some succeed, some None sentinel ────


@pytest.mark.asyncio
async def test_tier3_partial_fail_emits_event():
    """
    When some extraction tasks return None (sentinel) and others succeed:
    - Emits token_cascade_partial with correct success_map and failure_map.
    - Returns only the successfully extracted docs (failures retain Tier-1 content).
    - Does NOT emit token_cascade_failed (budget check passes).
    """
    # Build 5 sources, each with 1 document
    sources = [make_source(f'doc_{i} content ' * 10, file_id=f'f{i}', name=f'doc_{i}.md') for i in range(5)]
    event_emitter = AsyncMock()

    # Docs 0, 2, 4 fail (return None); docs 1, 3 succeed
    def mock_extract_side_effect(request, model_id, user_query, document_content, document_name, user, **kwargs):
        # document_name is "doc_N.md" — parse N
        idx = int(document_name.replace('doc_', '').replace('.md', ''))
        if idx in (0, 2, 4):
            return None  # Sentinel failure
        return f'extracted_{idx}'

    with (
        patch('open_webui.utils.middleware.Files') as MockFiles,
        patch('open_webui.utils.middleware.extract_relevant_content_from_document') as mock_extract,
        patch('open_webui.utils.middleware.estimate_sources_total_tokens') as mock_tokens,
    ):
        mock_tokens.side_effect = [
            9999,  # Tier 1: over budget
            50,  # Tier 3 post-gather: within budget
        ]
        MockFiles.get_file_by_id.return_value = None
        mock_extract.side_effect = mock_extract_side_effect

        result = await apply_token_budget_cascade(
            sources=sources,
            max_tokens=100,
            request=_mock_request(),
            body={'model': 'test', 'messages': [{'role': 'user', 'content': 'query'}]},
            user=MagicMock(),
            event_emitter=event_emitter,
        )

    # Should return 5 sources (all present; failures retain original content)
    assert len(result) == 5

    # Docs 1 and 3 should be extracted
    assert result[1]['document'] == ['extracted_1'], f'Doc 1 expected extracted, got {result[1]["document"]}'
    assert result[3]['document'] == ['extracted_3'], f'Doc 3 expected extracted, got {result[3]["document"]}'

    # Docs 0, 2, 4 should retain original Tier-1 content
    for idx in (0, 2, 4):
        assert result[idx]['document'][0].startswith(f'doc_{idx} content'), f'Doc {idx} should retain original content'

    # token_cascade_partial must have been emitted
    actions = _actions(event_emitter)
    assert 'token_cascade_partial' in actions, f'Expected token_cascade_partial, got: {actions}'

    # Find the partial event and inspect maps
    partial_event = next(ev for ev in _event_calls(event_emitter) if ev['data']['action'] == 'token_cascade_partial')
    failure_map = partial_event['data']['failure_map']
    success_map = partial_event['data']['success_map']

    # Failures: src 0/0, 2/0, 4/0
    assert '0/0' in failure_map, f'Expected 0/0 in failure_map: {failure_map}'
    assert '2/0' in failure_map, f'Expected 2/0 in failure_map: {failure_map}'
    assert '4/0' in failure_map, f'Expected 4/0 in failure_map: {failure_map}'

    # Successes: src 1/0, 3/0
    assert '1/0' in success_map, f'Expected 1/0 in success_map: {success_map}'
    assert '3/0' in success_map, f'Expected 3/0 in success_map: {success_map}'

    # No token_cascade_failed (budget was met)
    assert 'token_cascade_failed' not in actions, f'Unexpected token_cascade_failed: {actions}'


# ── Test 4: All succeed but still over budget → cascade failed ────


@pytest.mark.asyncio
async def test_tier3_success_but_over_budget_fallback():
    """
    When all extractions succeed but the total tokens are STILL over max_tokens
    after Tier-3 gather:
    - Emits token_cascade_failed with tokens and max_tokens fields.
    - Returns [] (empty list, not original sources).
    """
    source = make_source('big doc ' * 1000)
    event_emitter = AsyncMock()

    with (
        patch('open_webui.utils.middleware.Files') as MockFiles,
        patch('open_webui.utils.middleware.extract_relevant_content_from_document') as mock_extract,
        patch('open_webui.utils.middleware.estimate_sources_total_tokens') as mock_tokens,
    ):
        mock_tokens.side_effect = [
            9999,  # Tier 1: over budget
            9999,  # Tier 3 post-gather: STILL over budget
        ]
        MockFiles.get_file_by_id.return_value = None
        mock_extract.return_value = 'still very long extracted content that blows the budget'

        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=100,
            request=_mock_request(),
            body={'model': 'test', 'messages': [{'role': 'user', 'content': 'query'}]},
            user=MagicMock(),
            event_emitter=event_emitter,
        )

    assert result == [], f'Expected [] when over budget post-Tier3, got {result}'

    actions = _actions(event_emitter)
    assert 'token_cascade_failed' in actions, f'Expected token_cascade_failed, got: {actions}'

    # Verify shape of the failed event
    failed_event = next(ev for ev in _event_calls(event_emitter) if ev['data']['action'] == 'token_cascade_failed')
    assert failed_event['type'] == 'status'
    assert failed_event['data']['done'] is True
    assert 'tokens' in failed_event['data']
    assert 'max_tokens' in failed_event['data']
    assert failed_event['data']['max_tokens'] == 100


# ── Test 5: Sentinel contract — helper returns None on exception ──


@pytest.mark.asyncio
async def test_extract_relevant_returns_none_on_exception():
    """
    When generate_chat_completion raises an exception,
    extract_relevant_content_from_document must return None (sentinel),
    NOT the original document_content.
    """
    mock_request = MagicMock()

    with patch('open_webui.utils.middleware.generate_chat_completion') as mock_gen:
        mock_gen.side_effect = RuntimeError('network failure')

        result = await extract_relevant_content_from_document(
            request=mock_request,
            model_id='test-model',
            user_query='what is the answer?',
            document_content='ORIGINAL DOCUMENT CONTENT - should NOT be returned',
            document_name='test_doc.md',
            user=MagicMock(),
        )

    # MUST be None sentinel, not the original content
    assert result is None, (
        f'extract_relevant_content_from_document returned {result!r} on exception '
        f'but must return None sentinel (not original content)'
    )


# ── Test 6: All tasks raise Exception → only failed event, no empty-maps partial ─


@pytest.mark.asyncio
async def test_all_exception_emits_only_failed_event_no_partial():
    """
    When ALL extraction tasks raise an Exception (not None sentinel — they raise):
    - failure_map and success_map both remain empty (the exception path uses
      `continue`, so no map entry is written).
    - token_cascade_partial MUST NOT be emitted (empty-maps guard).
    - token_cascade_failed MUST be emitted (budget still exceeded).

    This verifies the W5-T2 empty-maps guard: suppress partial event when
    both maps are empty to prevent telemetry noise in the all-exception case.
    """
    source = make_source('doc content ' * 100)
    event_emitter = AsyncMock()

    with (
        patch('open_webui.utils.middleware.Files') as MockFiles,
        patch('open_webui.utils.middleware.extract_relevant_content_from_document') as mock_extract,
        patch('open_webui.utils.middleware.estimate_sources_total_tokens') as mock_tokens,
    ):
        # Tier 1: over budget; Tier 3: all raise, sources unchanged, still over budget
        mock_tokens.side_effect = [
            9999,  # Tier 1 check: over budget → proceed past Tier 1
            9999,  # Tier 3 post-gather: still over budget → emit token_cascade_failed
        ]
        MockFiles.get_file_by_id.return_value = None
        # Every task raises — this goes through the `isinstance(result, Exception)` branch
        # with `continue`, so neither map gets an entry.
        mock_extract.side_effect = RuntimeError('all tasks crashed')

        result = await apply_token_budget_cascade(
            sources=[source],
            max_tokens=100,
            request=_mock_request(),
            body={'model': 'test', 'messages': [{'role': 'user', 'content': 'query'}]},
            user=MagicMock(),
            event_emitter=event_emitter,
        )

    # Must return [] because still over budget
    assert result == [], f'Expected [] on all-exception + over budget, got {result!r}'

    actions = _actions(event_emitter)

    # token_cascade_failed must fire (budget exceeded)
    assert 'token_cascade_failed' in actions, f'Expected token_cascade_failed, got: {actions}'

    # token_cascade_partial must NOT fire (empty-maps guard)
    assert 'token_cascade_partial' not in actions, (
        f'token_cascade_partial must NOT fire when both maps are empty, got: {actions}. '
        f'This is the empty-maps guard introduced in W5-T2.'
    )

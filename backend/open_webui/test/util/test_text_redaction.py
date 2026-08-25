from open_webui.utils.text_redaction import (
    TEXT_REDACTION_CONTRACT,
    focus_text_redaction_messages,
    requests_text_redaction,
)


def test_exact_observed_masking_prompt_gets_non_reversible_output_contract():
    assert requests_text_redaction(
        'mask the decision that I pasted with token reference names, and paste it back for me.'
    )
    assert 'Never reproduce' in TEXT_REDACTION_CONTRACT
    assert 'visible reasoning section' in TEXT_REDACTION_CONTRACT
    assert 'substitution-only transformation' in TEXT_REDACTION_CONTRACT
    assert 'Return the transformed text only' in TEXT_REDACTION_CONTRACT
    assert 'Do not paraphrase' in TEXT_REDACTION_CONTRACT
    assert 'Begin immediately' in TEXT_REDACTION_CONTRACT


def test_common_english_and_chinese_text_redaction_requests_are_detected():
    assert requests_text_redaction('Anonymise these party names.')
    assert requests_text_redaction('Redact the identifiers in this document.')
    assert requests_text_redaction('請把這份判決匿名化並以代稱取代姓名。')
    assert requests_text_redaction('把當事人名稱遮罩後貼回來。')


def test_unrelated_masking_and_ordinary_rewrites_are_unchanged():
    assert not requests_text_redaction('Apply a circular mask to this image.')
    assert not requests_text_redaction('Summarise the decision for me.')
    assert not requests_text_redaction('Replace this paragraph with clearer wording.')


def test_masking_inference_keeps_source_and_current_turn_not_assistant_chatter():
    source = 'The requester alleges ' + ('source text ' * 40)
    messages = [
        {'role': 'system', 'content': 'stable policy'},
        {'role': 'user', 'content': 'help me digest a decision'},
        {'role': 'assistant', 'content': 'please paste it'},
        {'role': 'user', 'content': source},
        {'role': 'assistant', 'content': 'long analysis mentioning every original name'},
        {'role': 'user', 'content': 'is that result normal?'},
        {'role': 'assistant', 'content': 'more legal analysis'},
        {
            'role': 'user',
            'content': 'mask the decision with token reference names and paste it back',
        },
    ]

    focused = focus_text_redaction_messages(messages)

    assert [item['role'] for item in focused] == ['system', 'user', 'user']
    assert focused[1]['content'] == source
    assert focused[2]['content'] == messages[-1]['content']
    assert messages[4]['content'] == 'long analysis mentioning every original name'


def test_non_redaction_and_missing_source_keep_the_inference_history():
    ordinary = [
        {'role': 'user', 'content': 'a prior turn'},
        {'role': 'assistant', 'content': 'an answer'},
        {'role': 'user', 'content': 'summarise that answer'},
    ]
    missing_source = [
        {'role': 'user', 'content': 'short'},
        {
            'role': 'user',
            'content': 'mask the decision with token reference names and paste it back',
        },
    ]

    assert focus_text_redaction_messages(ordinary) == ordinary
    assert focus_text_redaction_messages(missing_source) == missing_source

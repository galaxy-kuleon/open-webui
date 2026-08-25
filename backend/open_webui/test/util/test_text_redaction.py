from open_webui.utils.text_redaction import (
    TEXT_REDACTION_CONTRACT,
    requests_text_redaction,
)


def test_exact_observed_masking_prompt_gets_non_reversible_output_contract():
    assert requests_text_redaction(
        'mask the decision that I pasted with token reference names, and paste it back for me.'
    )
    assert 'Never reproduce' in TEXT_REDACTION_CONTRACT
    assert 'visible reasoning section' in TEXT_REDACTION_CONTRACT
    assert 'must not reveal the original values' in TEXT_REDACTION_CONTRACT


def test_common_english_and_chinese_text_redaction_requests_are_detected():
    assert requests_text_redaction('Anonymise these party names.')
    assert requests_text_redaction('Redact the identifiers in this document.')
    assert requests_text_redaction('請把這份判決匿名化並以代稱取代姓名。')
    assert requests_text_redaction('把當事人名稱遮罩後貼回來。')


def test_unrelated_masking_and_ordinary_rewrites_are_unchanged():
    assert not requests_text_redaction('Apply a circular mask to this image.')
    assert not requests_text_redaction('Summarise the decision for me.')
    assert not requests_text_redaction('Replace this paragraph with clearer wording.')

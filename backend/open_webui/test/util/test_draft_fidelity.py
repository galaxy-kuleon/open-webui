from open_webui.utils.draft_fidelity import (
    DRAFT_DATE_DEFERRAL_CONTRACT,
    USER_TEXT_SOURCE_FIDELITY_CONTRACT,
    requests_draft_date_deferral,
    requests_user_text_source_fidelity,
)


def test_detects_chinese_email_rewrite_with_deferred_dates():
    prompt = "我要寫一封通知客戶商標已經publish的中文email，可以幫我改成與英文函差不多的內容嗎？日期先不管"

    assert requests_draft_date_deferral(prompt)


def test_detects_english_draft_with_date_placeholders():
    assert requests_draft_date_deferral(
        "Please rewrite this letter and leave the dates as placeholders."
    )


def test_does_not_trigger_for_date_discussion_without_drafting():
    assert not requests_draft_date_deferral("日期先不管，越南商標通常如何審查？")


def test_does_not_trigger_for_drafting_with_fixed_dates():
    assert not requests_draft_date_deferral(
        "請改寫這封通知信，公告日期是 2026 年 8 月 26 日。"
    )


def test_contract_covers_every_calendar_date_but_keeps_relative_duration():
    assert "every calendar date" in DRAFT_DATE_DEFERRAL_CONTRACT
    assert "anticipated calendar date" in DRAFT_DATE_DEFERRAL_CONTRACT
    assert "15 to 24 months from publication" in DRAFT_DATE_DEFERRAL_CONTRACT
    assert "No concrete year" in DRAFT_DATE_DEFERRAL_CONTRACT


def test_detects_bilingual_source_comparison():
    prompt = (
        "本公司就題述商標申請進一步來郵。" * 12
        + " We write to update the progress. 中文的和英文的內容哪裡不一樣"
    )

    assert requests_user_text_source_fidelity(prompt)


def test_detects_followup_rewrite_by_reference():
    assert requests_user_text_source_fidelity(
        "可以幫我改成與英文函差不多的內容嗎？"
    )


def test_does_not_treat_unrelated_question_as_source_transformation():
    assert not requests_user_text_source_fidelity("越南商標的實質審查通常要多久？")


def test_user_text_contract_forbids_jurisdiction_from_language():
    assert "Chinese-language text does not establish China" in USER_TEXT_SOURCE_FIDELITY_CONTRACT
    assert "Name a jurisdiction only" in USER_TEXT_SOURCE_FIDELITY_CONTRACT

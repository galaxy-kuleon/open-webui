from open_webui.utils.tool_intent import (
    allow_selected_tool,
    forced_socv2_submit_call,
    has_explicit_conversion_intent,
    is_socv2_submit_tool,
)


def test_socv2_submit_tool_identity_is_suffix_based():
    assert is_socv2_submit_tool('socv2_submit_conversion')
    assert is_socv2_submit_tool('mcp_soc_v2_submit_conversion')
    assert not is_socv2_submit_tool('socv2_conversion_status')
    assert not is_socv2_submit_tool('local_document_export')


def test_explicit_conversion_translation_and_export_are_allowed():
    prompts = (
        'convert the attached PDF to DOCX',
        'translate this attachment to Traditional Chinese',
        'export it as a downloadable Word document',
        '請把附件 PDF 轉成可編輯 DOCX',
        '翻譯附件為繁體中文',
        'using mcp_soc_v2_submit_conversion: translate to Chinese',
        'turn this attachment into a downloadable file',
    )
    assert all(has_explicit_conversion_intent(prompt) for prompt in prompts)
    assert all(allow_selected_tool('socv2_submit_conversion', prompt) for prompt in prompts)


def test_content_transformation_does_not_create_a_conversion_job():
    prompts = (
        'turn the attached note into a detailed note in Chinese',
        'turn this memo into a clear summary',
        'make a table from the attached image',
        'read this document and explain it',
        '把這份筆記整理成詳細中文說明',
    )
    assert all(not has_explicit_conversion_intent(prompt) for prompt in prompts)
    assert all(not allow_selected_tool('socv2_submit_conversion', prompt) for prompt in prompts)


def test_non_conversion_tools_are_untouched_by_policy():
    assert allow_selected_tool('web_search', 'turn this note into a detailed note')
    assert allow_selected_tool('socv2_conversion_status', 'check progress')


def test_explicit_current_file_translation_bypasses_probabilistic_selector():
    call = forced_socv2_submit_call(
        ['socv2_submit_conversion'],
        'using mcp_soc_v2_submit_conversion: translate to Chinese',
        ['file-123'],
    )

    assert call == {
        'name': 'socv2_submit_conversion',
        'parameters': {
            'file_id': 'file-123',
            'strict_file_id': True,
            'translate': True,
            'target_lang': 'zh-tw',
        },
    }


def test_deterministic_selector_stays_narrow_and_file_bound():
    tools = ['socv2_submit_conversion']
    assert forced_socv2_submit_call(tools, 'convert this PDF to DOCX', []) is None
    assert forced_socv2_submit_call(tools, 'convert this PDF to DOCX', ['a', 'b']) is None
    assert forced_socv2_submit_call(tools, 'summarize this PDF', ['a']) is None
    assert forced_socv2_submit_call(
        tools, 'translate this PDF to Klingon', ['a']
    ) is None

    assert forced_socv2_submit_call(tools, 'convert this PDF to DOCX', ['a']) == {
        'name': 'socv2_submit_conversion',
        'parameters': {'file_id': 'a', 'strict_file_id': True},
    }

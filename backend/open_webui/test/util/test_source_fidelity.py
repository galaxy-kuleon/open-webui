from open_webui.utils.source_fidelity import (
    ATTACHMENT_SOURCE_FIDELITY_PROMPT,
    should_apply_attachment_source_fidelity,
)


def test_direct_attachment_gets_source_fidelity_contract():
    assert should_apply_attachment_source_fidelity(has_files=True, hermes_handoff=False)
    assert 'Transcribe before interpreting' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'does not authorize new facts' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'clearly labelled source inventory' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'Inventory every visible region' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'perform a coverage pass' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'polished partial reading is not complete' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'even as a disclaimer' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'Never make a plausible completion' in ATTACHMENT_SOURCE_FIDELITY_PROMPT


def test_no_file_and_hermes_paths_are_unchanged():
    assert not should_apply_attachment_source_fidelity(has_files=False, hermes_handoff=False)
    assert not should_apply_attachment_source_fidelity(has_files=True, hermes_handoff=True)

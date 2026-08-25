from open_webui.utils.source_fidelity import (
    ATTACHMENT_SOURCE_FIDELITY_MAX_EVIDENCE_ITEMS,
    ATTACHMENT_SOURCE_FIDELITY_MAX_WORDS,
    ATTACHMENT_SOURCE_FIDELITY_PROMPT,
    ATTACHMENT_SOURCE_FIDELITY_TARGET_WORDS,
    ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX,
    should_apply_attachment_source_fidelity,
)


def test_direct_attachment_gets_source_fidelity_contract():
    assert should_apply_attachment_source_fidelity(has_files=True, hermes_handoff=False)
    assert 'Transcribe before interpreting' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'or new facts' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'does not authorize definitions' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'clearly labelled source inventory' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'Inventory every visible region' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'perform a coverage pass' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'polished partial reading is not complete' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'even as a disclaimer' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'Do not define or expand a source term' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'Never make a plausible completion' in ATTACHMENT_SOURCE_FIDELITY_PROMPT
    assert 'party-authored attachment as that author\'s claim' in (
        ATTACHMENT_SOURCE_FIDELITY_PROMPT
    )
    assert 'does not prove ownership, agency, licensing, custody' in (
        ATTACHMENT_SOURCE_FIDELITY_PROMPT
    )
    assert ATTACHMENT_SOURCE_FIDELITY_TARGET_WORDS == 650
    assert ATTACHMENT_SOURCE_FIDELITY_MAX_WORDS == 800
    assert ATTACHMENT_SOURCE_FIDELITY_MAX_EVIDENCE_ITEMS == 5
    assert 'Target no more than 650 words' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    assert 'never exceed 800 words' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    assert 'no more than 5 items' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    assert 'do not volunteer statute names or section numbers' in (
        ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    )
    assert 'unverified citation must not create a false impression' in (
        ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    )
    assert 'name or claim supplied only by the user is an allegation' in (
        ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    )
    assert 'Do not fill the gap from memory' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    assert 'proves what the party represented, not that the representation is true' in (
        ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    )
    assert 'should not be the sole basis' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX
    assert 'categorical yes or no' in ATTACHMENT_SOURCE_FIDELITY_TURN_SUFFIX


def test_no_file_and_hermes_paths_are_unchanged():
    assert not should_apply_attachment_source_fidelity(has_files=False, hermes_handoff=False)
    assert not should_apply_attachment_source_fidelity(has_files=True, hermes_handoff=True)

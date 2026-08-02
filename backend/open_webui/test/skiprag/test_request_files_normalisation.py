"""The producer side of the `metadata['files'] is None` incident.

`process_chat_payload` builds `metadata['files']` from
`form_data.pop('files', None)`. Because `.pop(key, default)` returns the
default only for an ABSENT key, an omitted `files` field and an explicit JSON
null are indistinguishable by the time any consumer sees the metadata — and
both used to arrive as None.

Path B then iterated it and raised, which aborted the handoff entirely. On
8083 that happened 21 times between 2026-07-24 and 2026-07-28; each of those
turns reached the model with no attachments at all, silently dropping every
file previously uploaded to that chat. Consumers now guard too, but the value
is fixed here, where it is produced.
"""

import logging

import pytest

from open_webui.utils.middleware import _normalised_request_files


def test_absent_or_null_files_becomes_an_empty_list(caplog):
    with caplog.at_level(logging.WARNING):
        assert _normalised_request_files(None) == []
    # An omitted optional field is ordinary, not an anomaly.
    assert caplog.records == []


def test_a_real_list_passes_through_unchanged():
    items = [{'type': 'file', 'id': 'file-1'}]
    assert _normalised_request_files(items) is items
    assert _normalised_request_files([]) == []


@pytest.mark.parametrize('invalid', [False, 0, '', {}, 'files', 3.5])
def test_schema_violations_are_logged_not_silently_swallowed(invalid, caplog):
    # Truthiness would turn `False` and `0` into "no files" without a trace,
    # letting a malformed payload pass for an ordinary fileless turn.
    with caplog.at_level(logging.WARNING):
        assert _normalised_request_files(invalid) == []
    assert caplog.records, f'{invalid!r} must not normalise silently'
    assert 'not a list' in caplog.records[0].getMessage()


def test_producer_result_never_reaches_consumers_as_none():
    # The property the whole incident turned on.
    for value in (None, [], [{'type': 'file'}], False, 0, 'x', {}):
        assert isinstance(_normalised_request_files(value), list)

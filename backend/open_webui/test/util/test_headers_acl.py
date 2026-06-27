"""Focused tests for forwarded user-group identity headers (Hermes ACL, issue #9).

Covers the additive ``user_groups`` behavior of ``include_user_info_headers``
(backward compatible when omitted) and the error-safe path of
``get_user_group_ids``. The DB-backed happy path of ``get_user_group_ids`` is
exercised by live 8083 verification (#14) rather than here.
"""

import asyncio
from types import SimpleNamespace

from open_webui.env import FORWARD_USER_INFO_HEADER_USER_GROUPS as GROUPS_HEADER
from open_webui.utils.headers import include_user_info_headers, get_user_group_ids


def _user():
    return SimpleNamespace(id="u1", name="Jane Doe", email="jane@example.com", role="user")


def test_no_groups_arg_is_backward_compatible():
    out = include_user_info_headers({"X-Existing": "keep"}, _user())
    assert out["X-Existing"] == "keep"
    assert GROUPS_HEADER not in out  # no groups header when not provided


def test_list_groups_joined_into_header():
    out = include_user_info_headers({}, _user(), user_groups=["g1", "g2"])
    assert out[GROUPS_HEADER] == "g1,g2"


def test_empty_groups_list_omits_header():
    out = include_user_info_headers({}, _user(), user_groups=[])
    assert GROUPS_HEADER not in out


def test_string_groups_passed_through():
    out = include_user_info_headers({}, _user(), user_groups="g9")
    assert out[GROUPS_HEADER] == "g9"


def test_get_user_group_ids_error_safe_for_missing_user():
    assert asyncio.run(get_user_group_ids(None)) == []
    assert asyncio.run(get_user_group_ids(SimpleNamespace(id=None))) == []

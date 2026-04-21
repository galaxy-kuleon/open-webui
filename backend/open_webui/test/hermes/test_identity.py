"""Behavioural tests for resolve_hermes_identity.

All cases mock Groups.get_groups_by_member_id to avoid hitting a real DB.
GroupModel is approximated as a simple namespace object because the function
only accesses .id and .meta attributes.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from open_webui.hermes.identity import resolve_hermes_identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(group_id: str, tenancy: str | None = None) -> SimpleNamespace:
    """Return a minimal GroupModel-like object with .id and .meta."""
    meta = {'tenancy': tenancy} if tenancy is not None else None
    return SimpleNamespace(id=group_id, meta=meta)


# ---------------------------------------------------------------------------
# Table-driven test cases
# ---------------------------------------------------------------------------

CASES = [
    # (label, user_dict, groups_return, expected_tenant_id, expected_user_id, expected_display_name, expected_locale)
    (
        'group_with_meta_tenancy',
        {'id': 'alice', 'name': 'Alice A', 'email': 'alice@test.com', 'role': 'user'},
        [_make_group('g-1', tenancy='acme')],
        'acme',
        'alice',
        'Alice A',
        'en',
    ),
    (
        'group_without_meta_tenancy',
        {'id': 'bob', 'name': 'Bob B', 'email': 'bob@test.com', 'role': 'user'},
        [_make_group('g-42')],
        'g-42',
        'bob',
        'Bob B',
        'en',
    ),
    (
        'no_groups',
        {'id': 'carol', 'name': 'Carol C', 'email': 'carol@test.com', 'role': 'user'},
        [],
        'carol',
        'carol',
        'Carol C',
        'en',
    ),
    (
        'user_none',
        None,
        None,  # Groups.get_groups_by_member_id will not be called
        None,
        None,
        None,
        None,
    ),
    (
        'user_with_explicit_locale',
        {'id': 'dave', 'name': 'Dave D', 'email': 'dave@test.com', 'role': 'user', 'locale': 'ja'},
        [_make_group('g-99', tenancy='corp-jp')],
        'corp-jp',
        'dave',
        'Dave D',
        'ja',
    ),
    (
        'tenancy_in_second_group_first_has_none',
        {'id': 'eve', 'name': 'Eve E', 'email': 'eve@test.com', 'role': 'user'},
        [_make_group('g-first'), _make_group('g-second', tenancy='beta-org')],
        'beta-org',
        'eve',
        'Eve E',
        'en',
    ),
]


@pytest.mark.parametrize(
    'label,user_dict,groups_return,expected_tenant_id,expected_user_id,expected_display_name,expected_locale',
    CASES,
    ids=[c[0] for c in CASES],
)
def test_resolve_hermes_identity(
    label,
    user_dict,
    groups_return,
    expected_tenant_id,
    expected_user_id,
    expected_display_name,
    expected_locale,
):
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        return_value=groups_return if groups_return is not None else [],
    ) as mock_groups:
        result = resolve_hermes_identity(user_dict)

        if user_dict is None:
            # (d) None input → None output; DB must not be called
            assert result is None
            mock_groups.assert_not_called()
            return

        assert result is not None, f'[{label}] expected a dict, got None'
        assert result['user_id'] == expected_user_id, f'[{label}] user_id mismatch'
        assert result['tenant_id'] == expected_tenant_id, f'[{label}] tenant_id mismatch'
        assert result['display_name'] == expected_display_name, f'[{label}] display_name mismatch'
        assert result['locale'] == expected_locale, f'[{label}] locale mismatch'

        # Sentinel guard: these literal strings must never appear as tenant_id
        assert result['tenant_id'] not in ('default', 'unknown'), f'[{label}] tenant_id must not be a sentinel literal'


# ---------------------------------------------------------------------------
# Extra: display_name falls back to user_id when name is absent
# ---------------------------------------------------------------------------


def test_display_name_fallback_to_user_id():
    user = {'id': 'frank', 'email': 'frank@test.com', 'role': 'user'}  # no "name" key
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        return_value=[],
    ):
        result = resolve_hermes_identity(user)
    assert result is not None
    assert result['display_name'] == 'frank'
    assert result['user_id'] == 'frank'
    assert result['tenant_id'] == 'frank'  # no groups → user_id


# ---------------------------------------------------------------------------
# Extra: meta is None (not just missing tenancy key) → still goes to tier 2
# ---------------------------------------------------------------------------


def test_meta_none_falls_to_tier2():
    group_no_meta = SimpleNamespace(id='gX', meta=None)
    user = {'id': 'grace', 'name': 'Grace G', 'email': 'grace@test.com', 'role': 'user'}
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        return_value=[group_no_meta],
    ):
        result = resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'gX'


# ---------------------------------------------------------------------------
# Extra: first-wins across non-contiguous tenancy-tagged groups
# Pins the invariant that groups=[no-meta, winA, winB] → tenant_id="winA".
# Guards against a future refactor writing tenancies[-1] instead of
# tenancies[0] — a 2-group test cannot catch that regression.
# ---------------------------------------------------------------------------


def test_first_wins_across_non_contiguous_tenancy_groups():
    groups = [
        _make_group('g-no-meta'),  # no meta.tenancy — falls through
        _make_group('g-win-a', tenancy='winA'),  # first with tenancy → wins
        _make_group('g-win-b', tenancy='winB'),  # should be ignored
    ]
    user = {'id': 'harry', 'name': 'Harry H', 'email': 'harry@test.com', 'role': 'user'}
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        return_value=groups,
    ):
        result = resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'winA', 'first group with meta.tenancy must win; got ' + repr(result['tenant_id'])

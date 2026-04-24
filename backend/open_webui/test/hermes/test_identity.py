"""Behavioural tests for resolve_hermes_identity.

All cases mock Groups.get_groups_by_member_id to avoid hitting a real DB.
GroupModel is approximated as a simple namespace object because the function
only accesses .id and .meta attributes.

resolve_hermes_identity is async (Groups.get_groups_by_member_id is async in
v0.9.1 — AsyncSession-based). All tests are marked asyncio and use await.
"""

from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest

from open_webui.hermes.identity import resolve_hermes_identity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(
    group_id: str,
    tenancy: str | None = None,
    shared_memory: bool | None = None,
) -> SimpleNamespace:
    """Return a minimal GroupModel-like object with .id and .meta."""
    meta: dict | None
    if tenancy is not None or shared_memory is not None:
        meta = {}
        if tenancy is not None:
            meta['tenancy'] = tenancy
        if shared_memory is not None:
            meta['shared_memory'] = shared_memory
    else:
        meta = None
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
        # updated: requires explicit shared_memory opt-in after Tier 2 hardening
        'group_without_meta_tenancy',
        {'id': 'bob', 'name': 'Bob B', 'email': 'bob@test.com', 'role': 'user'},
        [_make_group('g-42', shared_memory=True)],
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'label,user_dict,groups_return,expected_tenant_id,expected_user_id,expected_display_name,expected_locale',
    CASES,
    ids=[c[0] for c in CASES],
)
async def test_resolve_hermes_identity(
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
        new=AsyncMock(return_value=groups_return if groups_return is not None else []),
    ) as mock_groups:
        result = await resolve_hermes_identity(user_dict)

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


@pytest.mark.asyncio
async def test_display_name_fallback_to_user_id():
    user = {'id': 'frank', 'email': 'frank@test.com', 'role': 'user'}  # no "name" key
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['display_name'] == 'frank'
    assert result['user_id'] == 'frank'
    assert result['tenant_id'] == 'frank'  # no groups → user_id


# ---------------------------------------------------------------------------
# Extra: meta is None (not just missing tenancy key) → falls to tier 3 (user_id)
# updated: requires explicit shared_memory opt-in after Tier 2 hardening;
#          group with meta=None has no opt-in, so per-user isolation applies.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meta_none_falls_to_tier3():
    group_no_meta = SimpleNamespace(id='gX', meta=None)
    user = {'id': 'grace', 'name': 'Grace G', 'email': 'grace@test.com', 'role': 'user'}
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group_no_meta]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'grace'  # no shared_memory opt-in → per-user isolation


# ---------------------------------------------------------------------------
# Extra: first-wins across non-contiguous tenancy-tagged groups
# Pins the invariant that groups=[no-meta, winA, winB] → tenant_id="winA".
# Guards against a future refactor writing tenancies[-1] instead of
# tenancies[0] — a 2-group test cannot catch that regression.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_wins_across_non_contiguous_tenancy_groups():
    groups = [
        _make_group('g-no-meta'),  # no meta.tenancy — falls through
        _make_group('g-win-a', tenancy='winA'),  # first with tenancy → wins
        _make_group('g-win-b', tenancy='winB'),  # should be ignored
    ]
    user = {'id': 'harry', 'name': 'Harry H', 'email': 'harry@test.com', 'role': 'user'}
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=groups),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'winA', 'first group with meta.tenancy must win; got ' + repr(result['tenant_id'])


# ---------------------------------------------------------------------------
# New tests: Tier 2 explicit opt-in via shared_memory flag (Tier 2 hardening)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_2_requires_explicit_opt_in():
    """User in 1 group with NEITHER tenancy NOR shared_memory → tenant_id == user_id.

    This is the NEW behaviour: the old code would have returned the group's id
    (implicit Tier 2).  The new code requires explicit opt-in via shared_memory=True.
    """
    user = {'id': 'ivan', 'name': 'Ivan I', 'email': 'ivan@test.com', 'role': 'user'}
    group = _make_group('g-plain')  # no tenancy, no shared_memory — no opt-in
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'ivan', (
        'group without shared_memory opt-in must fall through to per-user isolation; got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_tier_2_opt_in_via_shared_memory_flag():
    """User in 1 group with meta = {"shared_memory": True} → tenant_id == group.id."""
    user = {'id': 'julia', 'name': 'Julia J', 'email': 'julia@test.com', 'role': 'user'}
    group = _make_group('g-shared', shared_memory=True)
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'g-shared', (
        'group with shared_memory=True must use group.id as tenant_id; got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_tier_1_wins_over_shared_memory_opt_in():
    """Tier 1 (tenancy string) beats Tier 2 (shared_memory flag).

    Groups: first has shared_memory=True (no tenancy); second has tenancy="acme".
    Expected: tenant_id == "acme" (Tier 1 from the second group wins over Tier 2
    from the first group, because the full pass collects both tiers simultaneously
    and Tier 1 exits immediately on match).
    """
    user = {'id': 'kim', 'name': 'Kim K', 'email': 'kim@test.com', 'role': 'user'}
    groups = [
        _make_group('g-opt-in', shared_memory=True),  # Tier 2 candidate
        _make_group('g-tenancy', tenancy='acme'),  # Tier 1 → must win
    ]
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=groups),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'acme', (
        'Tier 1 (tenancy string) must beat Tier 2 (shared_memory opt-in); got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_shared_memory_false_or_missing_does_not_opt_in():
    """Group with meta = {"shared_memory": False} does NOT opt in to shared memory.

    tenant_id must fall through to user_id (Tier 3).
    """
    user = {'id': 'leo', 'name': 'Leo L', 'email': 'leo@test.com', 'role': 'user'}
    group = _make_group('g-explicit-false', shared_memory=False)
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'leo', (
        'shared_memory=False must not opt in; expected per-user tenant_id; got ' + repr(result['tenant_id'])
    )


# ---------------------------------------------------------------------------
# P1/P2 review tests for commit ec71a53f9
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_memory_int_1_does_not_opt_in():
    """Group meta {"shared_memory": 1} → tenant_id == user_id.

    Strict ``is True`` check rejects truthy ints; admins must use bool.
    P1/P2 review of ec71a53f9.
    """
    user = {'id': 'mia', 'name': 'Mia M', 'email': 'mia@test.com', 'role': 'user'}
    group = SimpleNamespace(id='g-int-1', meta={'shared_memory': 1})
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'mia', (
        'shared_memory=1 (int) must not opt in; strict `is True` required; got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_shared_memory_string_true_does_not_opt_in():
    """Group meta {"shared_memory": "true"} → tenant_id == user_id.

    Strict ``is True`` check rejects truthy strings; admins must use bool.
    P1/P2 review of ec71a53f9.
    """
    user = {'id': 'noah', 'name': 'Noah N', 'email': 'noah@test.com', 'role': 'user'}
    group = SimpleNamespace(id='g-str-true', meta={'shared_memory': 'true'})
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'noah', (
        'shared_memory="true" (string) must not opt in; strict `is True` required; got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_shared_memory_none_in_meta_does_not_opt_in():
    """Group meta {"shared_memory": None} → tenant_id == user_id.

    None is falsy but also fails ``is True``; must fall to Tier 3.
    P1/P2 review of ec71a53f9.
    """
    user = {'id': 'olivia', 'name': 'Olivia O', 'email': 'olivia@test.com', 'role': 'user'}
    group = SimpleNamespace(id='g-none', meta={'shared_memory': None})
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'olivia', 'shared_memory=None must not opt in; got ' + repr(result['tenant_id'])


@pytest.mark.asyncio
async def test_shared_memory_key_absent_does_not_opt_in():
    """Group meta {"unrelated": "value"} → tenant_id == user_id.

    Absent key is treated identically to None/False — no implicit opt-in.
    P1/P2 review of ec71a53f9.
    """
    user = {'id': 'pete', 'name': 'Pete P', 'email': 'pete@test.com', 'role': 'user'}
    group = SimpleNamespace(id='g-unrelated', meta={'unrelated': 'value'})
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'pete', 'group without shared_memory key must not opt in; got ' + repr(
        result['tenant_id']
    )


@pytest.mark.asyncio
async def test_two_shared_memory_groups_winner_is_lexicographically_smallest_id():
    """User in 2 groups both with shared_memory=True → group with smallest id wins.

    "zzz-second" was updated *yesterday* (would win under ``updated_at DESC``
    — the DB default ordering), "aaa-first" was updated *today*.  The fix in
    Task A of P1/P2 review ec71a53f9 sorts groups by id before picking the
    Tier-2 winner, so "aaa-first" must win regardless of edit timestamps.

    This test MUST fail before the Task A sort change and pass after.
    P1/P2 review of ec71a53f9.
    """
    import time as _time

    now = int(_time.time())
    yesterday = now - 86400

    # DB returns updated_at DESC: zzz-second (yesterday's edit, but higher
    # updated_at simulation omitted — we control list order directly to
    # replicate what the DB would return).
    # zzz-second is listed first to simulate "most recently edited" ordering.
    group_zzz = SimpleNamespace(id='zzz-second', meta={'shared_memory': True}, updated_at=yesterday)
    group_aaa = SimpleNamespace(id='aaa-first', meta={'shared_memory': True}, updated_at=now)

    user = {'id': 'quinn', 'name': 'Quinn Q', 'email': 'quinn@test.com', 'role': 'user'}
    # Pass in DB order (zzz first — as if most recently edited).
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group_zzz, group_aaa]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'aaa-first', (
        'Tier-2 winner must be the group with lexicographically smallest id; got ' + repr(result['tenant_id'])
    )


@pytest.mark.asyncio
async def test_within_group_tenancy_wins_over_shared_memory():
    """Single group with meta = {"tenancy": "acme", "shared_memory": True} → tenant_id == "acme".

    Tier 1 (tenancy string) has unconditional priority over Tier 2
    (shared_memory group-id).  Even when both flags are present on the same
    group, the tenancy string is returned.
    P1/P2 review of ec71a53f9.
    """
    user = {'id': 'rosa', 'name': 'Rosa R', 'email': 'rosa@test.com', 'role': 'user'}
    group = SimpleNamespace(id='g-both', meta={'tenancy': 'acme', 'shared_memory': True})
    with patch(
        'open_webui.hermes.identity.Groups.get_groups_by_member_id',
        new=AsyncMock(return_value=[group]),
    ):
        result = await resolve_hermes_identity(user)
    assert result is not None
    assert result['tenant_id'] == 'acme', 'within-group tenancy string must beat shared_memory flag; got ' + repr(
        result['tenant_id']
    )

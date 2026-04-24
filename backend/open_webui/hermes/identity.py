"""Hermes identity resolution — pure function, no I/O side effects beyond the DB read.

Tenancy precedence (updated — intentional behaviour change for safety):

  1. **Tier 1** — First group whose ``meta["tenancy"]`` is a non-empty string wins.
     That string is used verbatim as the ``tenant_id``.

  2. **Tier 2** — If no group has ``meta.tenancy``, the first group whose
     ``meta["shared_memory"]`` is exactly ``True`` supplies its ``id`` as
     ``tenant_id``.  This is an **explicit opt-in**: groups without
     ``shared_memory: true`` do NOT implicitly share memory with their members.

     **Behaviour change from W1 spec**: previously any group membership
     silently widened the tenant scope to the first group's ``id``, causing
     cross-user memory leakage between members of unrelated groups (e.g. "QA
     team", "Marketing").  The new rule requires ``meta.shared_memory == True``
     on the group to activate group-scoped memory.  Existing groups that relied
     on the old implicit sharing must add ``shared_memory: true`` to keep
     working.

  3. **Tier 3** — If neither Tier 1 nor Tier 2 matched (including when the user
     belongs to no groups, or belongs only to groups without explicit opt-in),
     ``user["id"]`` is used, giving per-user isolation.

Returns ``None`` for an unauthenticated caller (``user is None``).
Never returns a sentinel string such as "default" or "unknown".
"""

from open_webui.models.groups import Groups


async def resolve_hermes_identity(user: dict | None) -> dict | None:
    """Resolve Open WebUI ``__user__`` to a Hermes identity dict.

    Parameters
    ----------
    user:
        The ``__user__`` dict supplied by the Open WebUI pipe framework:
        ``{"id": str, "name": str, "email": str, "role": str}``.
        May contain an optional ``"locale"`` key.
        ``None`` means the request is unauthenticated.

    Returns
    -------
    dict with keys ``user_id``, ``tenant_id``, ``display_name``, ``locale``
    when ``user`` is not ``None``, otherwise ``None``.
    """
    if user is None:
        return None

    user_id: str = str(user['id'])
    # Groups.get_groups_by_member_id is an async method in v0.9.1 (AsyncSession).
    groups = await Groups.get_groups_by_member_id(user_id)

    tenant_id: str = _resolve_tenant_id(user_id, groups)

    return {
        'user_id': user_id,
        'tenant_id': tenant_id,
        'display_name': str(user.get('name', user_id)),
        'locale': str(user.get('locale', 'en')),
    }


def _resolve_tenant_id(user_id: str, groups: list) -> str:
    """Three-tier tenancy resolution — no sentinel literals.

    Tier 1: first group with non-empty ``meta["tenancy"]`` string → return that string.
    Tier 2: first group with ``meta["shared_memory"] is True`` → return that group's id.
            Requires explicit opt-in; groups without this flag do NOT share memory.
    Tier 3: ``user_id`` — fires when no group matched Tier 1 or Tier 2, including
            when the user belongs to no groups at all.

    Tier 1 always beats Tier 2: if a group has both ``tenancy="acme"`` and
    ``shared_memory=True``, ``"acme"`` is returned.

    When multiple groups have ``shared_memory: True``, the group with the
    lexicographically smallest ``id`` wins, ensuring stability across admin
    edits.  (``Groups.get_groups_by_member_id`` sorts by ``updated_at DESC``
    — without an explicit re-sort here, an admin editing any group in the set
    would silently change which group becomes the Tier-2 winner.)

    Tier 1 order is also stabilised by the same sort; having two groups with
    *different* tenancy strings is operator error and is left unhandled — the
    first one (smallest id) wins consistently.

    TODO: add a Groups admin-UI checkbox for ``meta.shared_memory`` so
    operators don't have to edit raw JSON to opt in (no raw JSON editor in the
    Groups admin page yet — tracked separately).
    """
    first_shared_memory_group_id: str | None = None

    # Sort by group.id so Tier-2 winner is deterministic regardless of
    # updated_at ordering returned by the DB query.
    for group in sorted(groups, key=lambda g: str(g.id)):
        meta: dict = group.meta or {}

        # Tier 1: explicit tenancy string — highest priority, return immediately
        tenancy = meta.get('tenancy')
        if tenancy and isinstance(tenancy, str) and tenancy.strip():
            return tenancy.strip()

        # Tier 2 candidate: record first group with explicit shared_memory opt-in
        if first_shared_memory_group_id is None and meta.get('shared_memory') is True:
            first_shared_memory_group_id = str(group.id)

    # Tier 2: use first group that explicitly opted in to shared memory
    if first_shared_memory_group_id is not None:
        return first_shared_memory_group_id

    # Tier 3: per-user isolation — no group matched Tier 1 or Tier 2
    return user_id

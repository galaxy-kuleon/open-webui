"""Hermes identity resolution — pure function, no I/O side effects beyond the DB read.

Tenancy precedence (verbatim from W1 spec):
  1. First group whose ``meta["tenancy"]`` is a non-empty string wins.
  2. If no group has ``meta.tenancy``, the first group's ``id`` is used.
  3. If the user belongs to no groups, ``user["id"]`` is used.

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

    Tier 1: first group with non-empty ``meta["tenancy"]`` string.
    Tier 2: first group's ``id`` when no group has ``meta.tenancy``.
    Tier 3: ``user_id`` when the user belongs to no groups.
    """
    first_group_id: str | None = None

    for group in groups:
        if first_group_id is None:
            first_group_id = str(group.id)

        meta: dict = group.meta or {}
        tenancy = meta.get('tenancy')
        if tenancy and isinstance(tenancy, str) and tenancy.strip():
            return tenancy.strip()

    # Tier 2: fell through without finding meta.tenancy
    if first_group_id is not None:
        return first_group_id

    # Tier 3: no groups at all
    return user_id

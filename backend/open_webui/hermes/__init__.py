"""open_webui.hermes — identity and context helpers for the Hermes Agent pipe."""

# Placed here (package __init__) rather than a separate diagnostics.py because:
#   1. It is the only startup diagnostic for this package — a new module would
#      be over-engineering for a single function.
#   2. __init__.py is the natural public surface; callers import it as
#      `from open_webui.hermes import warn_unflagged_hermes_groups`.
#   3. Keeps main.py's lifespan section thin — it just calls this one symbol.

import logging

log = logging.getLogger(__name__)

_WARN_ID_CAP = 20  # max group ids to print before truncating


async def warn_unflagged_hermes_groups() -> None:
    """Log ONE warning at startup if any group lacks both ``tenancy`` and
    ``shared_memory`` in its meta.

    These groups implicitly shared memory before commit ec71a53f9 (Tier-2
    hardening).  After that commit they fall through to per-user isolation.
    Operators must add ``shared_memory: true`` to the group's meta to restore
    the old sharing behaviour.

    Defensive: if the Groups query throws (e.g. fresh install with empty DB,
    or migrations not yet run), we catch silently so startup is never blocked.
    """
    try:
        from open_webui.models.groups import Groups

        all_groups = await Groups.get_all_groups()
    except Exception:
        # Fresh install, DB not yet initialised, or import error — skip silently.
        return

    unflagged = [g for g in all_groups if not _group_has_hermes_opt_in(g.meta)]

    if not unflagged:
        return

    ids = [str(g.id) for g in unflagged]
    total = len(ids)

    if total > _WARN_ID_CAP:
        shown = ids[:_WARN_ID_CAP]
        remainder = total - _WARN_ID_CAP
        id_fragment = ', '.join(shown) + f' ...and {remainder} more ({total} total)'
    else:
        id_fragment = ', '.join(ids)

    log.warning(
        'WARNING: %d group(s) lack hermes shared_memory opt-in: %s. '
        'Members of these groups now resolve to per-user tenant '
        '(was group-shared before commit ec71a53f9). '
        "Add 'shared_memory: true' to group meta to restore sharing.",
        total,
        id_fragment,
    )


def _group_has_hermes_opt_in(meta: dict | None) -> bool:
    """Return True if the group's meta already has a hermes tenancy directive.

    A group is considered opted-in when it has either:
    - a non-empty ``meta["tenancy"]`` string  (Tier 1), or
    - ``meta["shared_memory"] is True``       (Tier 2 explicit opt-in).

    Groups that have neither were relying on the old implicit Tier-2 behaviour
    that was removed in commit ec71a53f9.
    """
    if not meta:
        return False
    tenancy = meta.get('tenancy')
    if tenancy and isinstance(tenancy, str) and tenancy.strip():
        return True
    return meta.get('shared_memory') is True

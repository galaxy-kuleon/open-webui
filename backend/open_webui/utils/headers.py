import logging
import time
from typing import Any, Optional
from urllib.parse import quote

import jwt
from open_webui.env import (
    FORWARD_USER_INFO_HEADER_JWT,
    FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS,
    FORWARD_USER_INFO_HEADER_JWT_SECRET,
    FORWARD_USER_INFO_HEADER_USER_EMAIL,
    FORWARD_USER_INFO_HEADER_USER_ID,
    FORWARD_USER_INFO_HEADER_USER_NAME,
    FORWARD_USER_INFO_HEADER_USER_ROLE,
    FORWARD_USER_INFO_HEADER_USER_GROUPS,
)

log = logging.getLogger(__name__)


def _mint_forward_user_jwt(user: Any) -> str:
    now = int(time.time())
    payload = {
        'sub': str(user.id),
        'email': str(user.email),
        'name': str(user.name),
        'role': str(user.role),
        'iss': 'open-webui',
        'iat': now,
        'exp': now + FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, FORWARD_USER_INFO_HEADER_JWT_SECRET, algorithm='HS256')


def include_user_info_headers(headers: dict, user: Optional[Any] = None, user_groups=None) -> dict:
    """
    Forward user identity to external backends: signed JWT in
    FORWARD_USER_INFO_HEADER_JWT if FORWARD_USER_INFO_HEADER_JWT_SECRET is set;
    otherwise the legacy X-OpenWebUI-User-* headers.

    ``user_groups`` (optional) is a list of stable OpenWebUI group IDs, or a
    pre-joined comma-separated string. When non-empty it is forwarded as
    ``X-OpenWebUI-User-Groups`` on the legacy header path so downstream services
    (e.g. the Hermes agent) can apply per-group ACLs. Omitting it preserves the
    previous behavior exactly (no groups header).
    """
    if user is None:
        return headers

    if FORWARD_USER_INFO_HEADER_JWT_SECRET:
        try:
            token = _mint_forward_user_jwt(user)
            return {**headers, FORWARD_USER_INFO_HEADER_JWT: token}
        except Exception:
            log.exception(
                'Failed to mint %s; falling back to plain user-info headers.',
                FORWARD_USER_INFO_HEADER_JWT,
            )

    result = {
        **headers,
        FORWARD_USER_INFO_HEADER_USER_NAME: quote(user.name, safe=' '),
        FORWARD_USER_INFO_HEADER_USER_ID: user.id,
        FORWARD_USER_INFO_HEADER_USER_EMAIL: user.email,
        FORWARD_USER_INFO_HEADER_USER_ROLE: user.role,
    }
    if user_groups:
        if isinstance(user_groups, (list, tuple, set)):
            groups_value = ','.join(str(g) for g in user_groups if g)
        else:
            groups_value = str(user_groups)
        if groups_value:
            result[FORWARD_USER_INFO_HEADER_USER_GROUPS] = groups_value
    return result


async def get_user_group_ids(user) -> list:
    """Return the stable OpenWebUI group IDs the given user belongs to.

    Used to forward ``X-OpenWebUI-User-Groups`` to downstream models so their
    skill ACLs can key on group membership. Returns ``[]`` on any error so
    identity forwarding never breaks the request path.
    """
    if not user or not getattr(user, 'id', None):
        return []
    try:
        from open_webui.models.groups import Groups

        groups = await Groups.get_groups_by_member_id(user.id)
        return [g.id for g in groups if getattr(g, 'id', None)]
    except Exception:
        return []


def get_custom_headers(custom_headers: dict, user=None, metadata: dict = None) -> dict:
    if not custom_headers or not isinstance(custom_headers, dict):
        return {}

    metadata = metadata or {}
    template_vars = {
        '{{CHAT_ID}}': metadata.get('chat_id', '') or '',
        '{{MESSAGE_ID}}': metadata.get('message_id', '') or '',
        '{{USER_ID}}': (user.id if user else '') or '',
        '{{USER_NAME}}': (user.name if user else '') or '',
        '{{USER_EMAIL}}': (user.email if user else '') or '',
        '{{USER_ROLE}}': (user.role if user else '') or '',
    }

    parsed_headers = {}
    for key, value in custom_headers.items():
        if not isinstance(value, str):
            value = str(value)
        for token, val in template_vars.items():
            value = value.replace(token, val)
        parsed_headers[key] = value

    return parsed_headers

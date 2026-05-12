"""Feedback outbox draining for the OpenWebUI → Hermes bridge."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from open_webui.models.feedback_outbox import FeedbackOutboxes
from open_webui.utils.hermes_bridge import (
    HermesBridgeClient,
    feedback_delete_purge_enabled,
    get_bridge_config,
    outbox_drain_enabled,
    outbox_worker_enabled,
)

log = logging.getLogger(__name__)


async def drain_feedback_outbox_once(app_config: Any = None, *, batch_size: int = 50) -> dict[str, int]:
    cfg = get_bridge_config(app_config)
    drain_all = outbox_drain_enabled(app_config)
    drain_deletes = feedback_delete_purge_enabled(app_config)
    if not drain_all and not drain_deletes:
        return {"claimed": 0, "sent": 0, "failed": 0, "skipped": 1}
    rows = await FeedbackOutboxes.claim_batch(
        limit=batch_size,
        event_types=None if drain_all else ['deleted'],
    )
    result = {"claimed": len(rows), "sent": 0, "failed": 0, "skipped": 0}
    if not rows:
        return result

    async with HermesBridgeClient(cfg) as client:
        for row in rows:
            current = await FeedbackOutboxes.get_claimed_event(row.event_id, lease_until=row.lease_until)
            if current is None:
                result["skipped"] += 1
                continue
            payload = current.payload
            if current.event_type != 'deleted':
                if payload.get('redacted') or 'feedback' not in payload:
                    if await FeedbackOutboxes.mark_sent(
                        current.event_id,
                        lease_until=current.lease_until,
                        redaction_reason='superseded_feedback_delete',
                    ):
                        result["skipped"] += 1
                    continue
                if await FeedbackOutboxes.has_superseding_delete(
                    current.feedback_id,
                    current.feedback_version,
                ):
                    if await FeedbackOutboxes.mark_sent(
                        current.event_id,
                        lease_until=current.lease_until,
                        redaction_reason='superseded_feedback_delete',
                    ):
                        result["skipped"] += 1
                    continue
            try:
                role = str(payload.get("actor_role") or "user")
                await client.post_feedback_event(payload, user_id=current.user_id, role=role)
                if await FeedbackOutboxes.mark_sent(current.event_id, lease_until=current.lease_until):
                    result["sent"] += 1
                    log.info(
                        "hermes_feedback_outbox sent event_id=%s user_id=%s event_type=%s",
                        current.event_id,
                        current.user_id,
                        current.event_type,
                    )
            except Exception as exc:
                if await FeedbackOutboxes.mark_failed(current.event_id, str(exc), lease_until=current.lease_until):
                    result["failed"] += 1
                    log.warning(
                        "hermes_feedback_outbox failed event_id=%s user_id=%s error=%s",
                        current.event_id,
                        current.user_id,
                        exc,
                    )
    return result


async def feedback_outbox_worker_loop(app: Any) -> None:
    """Drain loop; reliability comes from persisted rows + idempotent Hermes writes."""
    interval = float(os.environ.get("OPENWEBUI_HERMES_OUTBOX_INTERVAL_SECONDS", "5"))
    batch_size = int(os.environ.get("OPENWEBUI_HERMES_OUTBOX_BATCH_SIZE", "50"))
    while True:
        try:
            config = getattr(app.state, "config", None)
            if outbox_worker_enabled(config):
                await drain_feedback_outbox_once(config, batch_size=batch_size)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("hermes_feedback_outbox drain iteration failed: %s", exc)
        await asyncio.sleep(interval)

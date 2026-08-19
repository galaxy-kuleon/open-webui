"""Constrained same-origin proxy for signed Hermes export artifacts."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.utils.hermes_bridge import get_bridge_config
from starlette.background import BackgroundTask


log = logging.getLogger(__name__)
router = APIRouter()

ARTIFACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILENAME_CHARS = 255
FORWARDED_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-length",
        "content-type",
        "x-content-type-options",
    }
)


def build_artifact_target(
    base_url: str,
    artifact_id: str,
    filename: str,
    expires_epoch: int,
    signature: str,
) -> str | None:
    """Return an exact fixed-origin Hermes URL, or ``None`` for bad capability data."""
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        return None
    if not SIGNATURE_RE.fullmatch(signature):
        return None
    if (
        not filename
        or len(filename) > MAX_FILENAME_CHARS
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        return None
    if expires_epoch <= 0:
        return None
    clean_base = base_url.strip().rstrip("/")
    if not clean_base.startswith(("http://", "https://")):
        return None
    return (
        f"{clean_base}/v1/artifacts/{artifact_id}/{quote(filename, safe='')}"
        f"/download/{expires_epoch}/{signature}"
    )


@router.api_route(
    "/v1/artifacts/{artifact_id}/{filename}/download/{expires_epoch}/{signature}",
    methods=["GET", "HEAD"],
)
async def proxy_signed_artifact(
    artifact_id: str,
    filename: str,
    expires_epoch: int,
    signature: str,
    request: Request,
):
    """Stream one signed artifact from the configured internal Hermes origin."""
    config = get_bridge_config(request.app.state.config)
    target = build_artifact_target(
        config.url, artifact_id, filename, expires_epoch, signature
    )
    if target is None:
        return JSONResponse({"error": "invalid artifact link"}, status_code=400)

    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=config.timeout),
        trust_env=False,
    )
    try:
        upstream = await session.request(
            request.method,
            target,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )
    except Exception:
        await session.close()
        log.exception("hermes_artifact_proxy upstream request failed")
        return JSONResponse({"error": "artifact service unavailable"}, status_code=502)

    headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in FORWARDED_RESPONSE_HEADERS
    }
    headers["X-Hermes-Artifact-Proxy"] = "same-origin"

    async def cleanup() -> None:
        upstream.release()
        await session.close()

    return StreamingResponse(
        content=upstream.content.iter_any(),
        status_code=upstream.status,
        headers=headers,
        background=BackgroundTask(cleanup),
    )

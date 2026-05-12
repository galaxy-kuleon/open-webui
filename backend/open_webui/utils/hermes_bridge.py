"""OpenWebUI-side client utilities for the Hermes memory bridge."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value.strip().lower() in TRUE_VALUES
    return default


def _config_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return bool(value)


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _env_explicit(*names: str) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return None


def _runtime_config_bool(value: Any, *, default: bool, env_names: tuple[str, ...]) -> bool:
    env_value = _env_explicit(*env_names)
    if env_value is not None:
        return env_value.strip().lower() in TRUE_VALUES
    return _config_bool(value, default=default)


def _runtime_config_str(value: Any, *, default: str, env_names: tuple[str, ...]) -> str:
    env_value = _env_explicit(*env_names)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    if value is None:
        return default
    return str(value)


def _runtime_config_float(value: Any, *, default: float, env_names: tuple[str, ...]) -> float:
    env_value = _env_explicit(*env_names)
    if env_value is not None and env_value.strip():
        return float(env_value.strip())
    if value is None:
        return default
    return float(value)


HERMES_BRIDGE_ENABLED = _env_bool(
    "OPENWEBUI_HERMES_BRIDGE_ENABLED",
    "HERMES_BRIDGE_ENABLED",
    default=False,
)
HERMES_MEMORY_BRIDGE_ENABLED = _env_bool(
    "OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED",
    "HERMES_MEMORY_BRIDGE_ENABLED",
    default=False,
)
HERMES_OUTBOX_DRAIN_ENABLED = _env_bool(
    "OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED",
    "HERMES_OUTBOX_DRAIN_ENABLED",
    default=HERMES_BRIDGE_ENABLED,
)
HERMES_DELETE_PURGE_ENABLED = _env_bool(
    "OPENWEBUI_HERMES_DELETE_PURGE_ENABLED",
    "HERMES_DELETE_PURGE_ENABLED",
    default=True,
)
HERMES_BRIDGE_URL = _env_first(
    "OPENWEBUI_HERMES_BRIDGE_URL",
    "HERMES_BRIDGE_URL",
    default="http://hermes-gateway:8642",
)
HERMES_BRIDGE_API_KEY = _env_first(
    "OPENWEBUI_HERMES_BRIDGE_API_KEY",
    "HERMES_BRIDGE_API_KEY",
    default="",
)
HERMES_BRIDGE_TIMEOUT = float(_env_first("OPENWEBUI_HERMES_BRIDGE_TIMEOUT", "HERMES_BRIDGE_TIMEOUT", default="10"))
HERMES_BRIDGE_MODEL_IDS = tuple(
    item.strip()
    for item in _env_first("OPENWEBUI_HERMES_BRIDGE_MODEL_IDS", "HERMES_BRIDGE_MODEL_IDS", default="hermes-agent").split(",")
    if item.strip()
)


@dataclass(frozen=True)
class HermesBridgeConfig:
    enabled: bool = HERMES_BRIDGE_ENABLED
    memory_enabled: bool = HERMES_MEMORY_BRIDGE_ENABLED
    outbox_drain_enabled: bool = HERMES_OUTBOX_DRAIN_ENABLED
    delete_purge_enabled: bool = HERMES_DELETE_PURGE_ENABLED
    url: str = HERMES_BRIDGE_URL
    api_key: str = HERMES_BRIDGE_API_KEY
    timeout: float = HERMES_BRIDGE_TIMEOUT


@dataclass(frozen=True)
class HermesMemoryListPage:
    memories: list[dict[str, Any]]
    may_be_capped: bool


def get_bridge_config(app_config: Any = None) -> HermesBridgeConfig:
    """Return bridge config, with explicit env values overriding persisted config."""
    base = HermesBridgeConfig()
    source = app_config if app_config is not None else object()
    return HermesBridgeConfig(
        enabled=_runtime_config_bool(
            getattr(source, "HERMES_BRIDGE_ENABLED", base.enabled),
            default=base.enabled,
            env_names=("OPENWEBUI_HERMES_BRIDGE_ENABLED", "HERMES_BRIDGE_ENABLED"),
        ),
        memory_enabled=_runtime_config_bool(
            getattr(source, "HERMES_MEMORY_BRIDGE_ENABLED", base.memory_enabled),
            default=base.memory_enabled,
            env_names=("OPENWEBUI_HERMES_MEMORY_BRIDGE_ENABLED", "HERMES_MEMORY_BRIDGE_ENABLED"),
        ),
        outbox_drain_enabled=_runtime_config_bool(
            getattr(source, "HERMES_OUTBOX_DRAIN_ENABLED", base.outbox_drain_enabled),
            default=base.outbox_drain_enabled,
            env_names=("OPENWEBUI_HERMES_OUTBOX_DRAIN_ENABLED", "HERMES_OUTBOX_DRAIN_ENABLED"),
        ),
        delete_purge_enabled=_runtime_config_bool(
            getattr(source, "HERMES_DELETE_PURGE_ENABLED", base.delete_purge_enabled),
            default=base.delete_purge_enabled,
            env_names=("OPENWEBUI_HERMES_DELETE_PURGE_ENABLED", "HERMES_DELETE_PURGE_ENABLED"),
        ),
        url=_runtime_config_str(
            getattr(source, "HERMES_BRIDGE_URL", base.url),
            default=base.url,
            env_names=("OPENWEBUI_HERMES_BRIDGE_URL", "HERMES_BRIDGE_URL"),
        ),
        api_key=_runtime_config_str(
            getattr(source, "HERMES_BRIDGE_API_KEY", base.api_key),
            default=base.api_key,
            env_names=("OPENWEBUI_HERMES_BRIDGE_API_KEY", "HERMES_BRIDGE_API_KEY"),
        ),
        timeout=_runtime_config_float(
            getattr(source, "HERMES_BRIDGE_TIMEOUT", base.timeout),
            default=base.timeout,
            env_names=("OPENWEBUI_HERMES_BRIDGE_TIMEOUT", "HERMES_BRIDGE_TIMEOUT"),
        ),
    )


def memory_bridge_enabled(app_config: Any = None) -> bool:
    cfg = get_bridge_config(app_config)
    return cfg.enabled and cfg.memory_enabled and bool(cfg.url) and bool(cfg.api_key)


def memory_bridge_misconfigured(app_config: Any = None) -> bool:
    cfg = get_bridge_config(app_config)
    return cfg.enabled and cfg.memory_enabled and (not cfg.url or not cfg.api_key)


def feedback_outbox_capture_enabled(app_config: Any = None) -> bool:
    cfg = get_bridge_config(app_config)
    return cfg.enabled


def outbox_drain_enabled(app_config: Any = None) -> bool:
    cfg = get_bridge_config(app_config)
    return cfg.enabled and cfg.outbox_drain_enabled and bool(cfg.url) and bool(cfg.api_key)


def feedback_delete_purge_enabled(app_config: Any = None) -> bool:
    cfg = get_bridge_config(app_config)
    return cfg.delete_purge_enabled and bool(cfg.url) and bool(cfg.api_key)


def outbox_worker_enabled(app_config: Any = None) -> bool:
    return outbox_drain_enabled(app_config) or feedback_delete_purge_enabled(app_config)


def should_bypass_openwebui_memory_injection(app_config: Any, model_id: Optional[str]) -> bool:
    """Avoid double memory injection for the Hermes-backed model in bridge mode."""
    if not memory_bridge_enabled(app_config) or not model_id:
        return False
    normalized = model_id.strip()
    return any(normalized == item or normalized.startswith(f"{item}:") for item in HERMES_BRIDGE_MODEL_IDS)


def build_hermes_headers(
    user_id: str,
    *,
    role: Optional[str] = None,
    chat_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    config: Optional[HermesBridgeConfig] = None,
) -> dict[str, str]:
    cfg = config or HermesBridgeConfig()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-OpenWebUI-User-Id": user_id,
    }
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    if role:
        headers["X-OpenWebUI-User-Role"] = role
    if chat_id:
        headers["X-OpenWebUI-Chat-Id"] = chat_id
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def build_memory_search_result(memories: list[dict[str, Any]], *, query: str = "") -> dict[str, list[list[Any]]]:
    """Return OpenWebUI vector-search-compatible shape from Hermes memories."""
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, Any]] = []
    distances: list[float] = []
    for memory in memories:
        memory_id = memory.get("id") or memory.get("memory_id")
        if not memory_id:
            continue
        content = str(memory.get("content") or "")
        ids.append(str(memory_id))
        docs.append(content)
        metas.append(
            {
                "created_at": memory.get("created_at"),
                "updated_at": memory.get("updated_at"),
                "type": memory.get("type"),
                "confidence": memory.get("confidence"),
            }
        )
        score = memory.get("score")
        distances.append(float(score) if score is not None else (1.0 if not query.strip() else 0.95))
    return {"ids": [ids], "documents": [docs], "metadatas": [metas], "distances": [distances]}


class HermesBridgeClient:
    def __init__(self, config: Optional[HermesBridgeConfig] = None, http_client: Any = None):
        self.config = config or HermesBridgeConfig()
        self._http_client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "HermesBridgeClient":
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(base_url=self.config.url.rstrip("/"), timeout=self.config.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        user_id: str,
        role: Optional[str] = None,
        chat_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(base_url=self.config.url.rstrip("/"), timeout=self.config.timeout)
        response = await self._http_client.request(
            method,
            path,
            headers=build_hermes_headers(
                user_id,
                role=role,
                chat_id=chat_id,
                idempotency_key=idempotency_key,
                config=self.config,
            ),
            json=json_body,
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"result": data}

    async def post_feedback_event(self, event: dict[str, Any], *, user_id: str, role: str = "user") -> dict[str, Any]:
        event_id = str(event.get("event_id") or event.get("idempotency_key") or "")
        return await self._request(
            "POST",
            "/api/openwebui/feedback-events",
            user_id=user_id,
            role=role,
            idempotency_key=event_id or None,
            json_body=event,
        )

    async def list_memories(self, *, user_id: str, query: Optional[str] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
        page = await self.list_memories_page(user_id=user_id, query=query, limit=limit)
        return page.memories

    async def list_memories_page(
        self,
        *,
        user_id: str,
        query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> HermesMemoryListPage:
        data = await self._request(
            "GET",
            "/api/openwebui/memories",
            user_id=user_id,
            params={k: v for k, v in {"q": query, "limit": limit}.items() if v is not None},
        )
        memories = list(data.get("memories") or [])
        return HermesMemoryListPage(
            memories=memories,
            may_be_capped=_memory_list_may_be_capped(data, memories),
        )

    async def create_memory(self, *, user_id: str, content: str, memory_type: str = "imported") -> dict[str, Any]:
        data = await self._request(
            "POST",
            "/api/openwebui/memories",
            user_id=user_id,
            json_body={"content": content, "type": memory_type, "source": "openwebui-ui"},
        )
        return dict(data.get("memory") or data)

    async def update_memory(self, *, user_id: str, memory_id: str, content: str) -> Optional[dict[str, Any]]:
        try:
            data = await self._request(
                "POST",
                "/api/openwebui/memories",
                user_id=user_id,
                json_body={
                    "id": memory_id,
                    "content": content,
                    "source": "openwebui-ui",
                    "require_existing": True,
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            if exc.response.status_code == 409:
                detail = getattr(exc.response, "text", "") or "memory is retracted"
                raise ValueError(detail) from exc
            raise
        return dict(data.get("memory") or data)

    async def delete_memory(self, *, user_id: str, memory_id: str) -> bool:
        data = await self._request("DELETE", f"/api/openwebui/memories/{memory_id}", user_id=user_id)
        return bool(data.get("deleted"))


class HermesMalformedMemoryResponseError(RuntimeError):
    """Raised when Hermes returns a memory payload without an identifier."""


def memory_model_from_bridge(payload: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    memory_id = payload.get("id") or payload.get("memory_id")
    if not memory_id:
        raise HermesMalformedMemoryResponseError("Hermes memory response is missing id or memory_id")
    return {
        "id": str(memory_id),
        "user_id": str(payload.get("user_id") or ""),
        "content": str(payload.get("content") or ""),
        "created_at": int(payload.get("created_at") or payload.get("updated_at") or now),
        "updated_at": int(payload.get("updated_at") or payload.get("created_at") or now),
    }


def _memory_list_may_be_capped(data: dict[str, Any], memories: list[dict[str, Any]]) -> bool:
    if bool(data.get("has_more")) or bool(data.get("truncated")):
        return True
    if data.get("next_cursor") or data.get("cursor"):
        return True

    if not memories:
        return False

    total = data.get("total")
    if total is None:
        total = data.get("total_count")
    if total is not None:
        try:
            return int(total) > len(memories)
        except (TypeError, ValueError):
            return True

    if data.get("has_more") is False or data.get("truncated") is False:
        return False

    return True

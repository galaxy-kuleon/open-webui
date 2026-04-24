"""
title: Hermes Agent
description: Proxies chat to hermes-agent API server with SSE streaming and tool progress
author: kuleon-galaxy
version: 0.1.0
"""
# Note: httpx is a core openwebui dependency (imported by routers/retrieval, tests,
# etc.) so a `requirements:` frontmatter line would trigger a redundant pip install
# that fails under uv-managed venvs. Do NOT add it.

import json
import logging
from typing import AsyncGenerator

import httpx
from pydantic import BaseModel, Field, model_validator

from open_webui.hermes.identity import resolve_hermes_identity

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default status event action — reuse "agent_skill" so existing
# AgentSkillStatus.svelte renders tool progress with zero frontend changes.
# ---------------------------------------------------------------------------
_STATUS_ACTION = 'agent_skill'


class Pipe:
    """Hermes Agent pipe function — manifold SSE proxy.

    Appears as one or more models in the Open WebUI model dropdown
    (one per hermes profile).  Proxies chat completions to the hermes
    API server and translates tool progress events into Open WebUI
    status events for real-time UI feedback.
    """

    class Valves(BaseModel):
        hermes_api_url: str = Field(
            default='http://localhost:8642',
            description='Hermes API server base URL (e.g. http://localhost:8642)',
        )
        hermes_api_key: str = Field(
            default='',
            description='Bearer token for hermes API (matches API_SERVER_KEY). Required for authenticated session continuity.',
            json_schema_extra={'input': {'type': 'password'}},
        )
        request_timeout: int = Field(
            default=600,
            description='HTTP request timeout in seconds (long for agent tasks)',
        )
        health_check_timeout: float = Field(
            default=2.0,
            description='Timeout for health check / model discovery (seconds)',
        )

        @model_validator(mode='after')
        def _warn_if_key_absent(self) -> 'Pipe.Valves':
            # An empty key is allowed — session continuity is silently disabled.
            # Whitespace-only strings are normalised to empty to prevent subtle bugs.
            if self.hermes_api_key and not self.hermes_api_key.strip():
                object.__setattr__(self, 'hermes_api_key', '')
            return self

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # Manifold: expose hermes profiles as sub-models
    # ------------------------------------------------------------------
    # Hermes runs its own skill router, tool loop and file I/O via
    # _resolve_file_paths/_inject_file_context — middleware MUST NOT inject
    # agent-skill keyword intercepts or skip_rag/RAG content for these models.
    # functions.get_function_models() forwards this `meta` dict into
    # model['info']['meta'] so middleware can read .capabilities.delegated_orchestration.
    _HERMES_PIPE_META: dict = {
        'capabilities': {
            'delegated_orchestration': True,
        },
    }

    async def pipes(self) -> list[dict]:
        """Query hermes /v1/models and return one entry per profile.

        Falls back to a single default entry if hermes returns only one
        model, or returns [] if hermes is unreachable (models disappear
        from dropdown — no error shown).
        """
        url = self.valves.hermes_api_url.rstrip('/')
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=self.valves.health_check_timeout) as client:
                r = await client.get(f'{url}/v1/models', headers=headers)
                if r.status_code != 200:
                    if r.status_code == 401:
                        log.error(
                            'Hermes /v1/models returned HTTP 401 — hermes_api_key is wrong or '
                            'expired. Session memory continuity will be broken. '
                            'Update the hermes_api_key valve to match API_SERVER_KEY on the Hermes server.'
                        )
                    else:
                        log.warning('Hermes /v1/models returned HTTP %s', r.status_code)
                    return []
                data = r.json().get('data', [])
                if not data:
                    return [{'id': 'default', 'name': 'Hermes Agent', 'meta': self._HERMES_PIPE_META}]
                return [
                    {
                        'id': m.get('id', 'default'),
                        'name': m.get('id', 'Hermes Agent'),
                        'meta': self._HERMES_PIPE_META,
                    }
                    for m in data
                ]
        except httpx.ConnectError as e:
            log.error('Hermes unreachable for model discovery (%s) — is the server running? %s', url, e)
            return []
        except httpx.TimeoutException as e:
            log.warning('Hermes model discovery timed out (%s): %s', url, e)
            return []
        except Exception as e:
            log.warning('Hermes model discovery failed unexpectedly: %s', e)
            return []

    # ------------------------------------------------------------------
    # Main pipe — SSE proxy with event translation
    # ------------------------------------------------------------------
    async def pipe(
        self,
        body: dict,
        __event_emitter__=None,
        __user__: dict | None = None,
        __chat_id__: str | None = None,
        __files__: list | None = None,
        __metadata__: dict | None = None,
        **kwargs,
    ) -> AsyncGenerator:
        """Forward chat to hermes and stream the response.

        Yields OpenAI-format chunk dicts for content streaming.
        Emits status events via __event_emitter__ for tool progress.
        Falls back to stateless request-body history when no API key is
        configured, because Hermes only accepts session continuation on
        authenticated requests.

        Connection vs. pipe:
            Hermes exposes a standard OpenAI-compatible API on /v1/chat/completions
            and /v1/models, so users who need none of the four pipe-only features
            below can configure Hermes as a plain OpenAI Connection and skip this
            pipe entirely. Use this pipe only when any of the following apply:

            1. tool-progress translation — decodes custom ``event: hermes.tool.progress``
               SSE frames into __event_emitter__ status events. Open WebUI's built-in
               OpenAI SSE parser silently drops non-``data:`` SSE event types; this
               pipe rescues them.
            2. file-path injection — resolves Open WebUI upload IDs to absolute
               filesystem paths and injects them into the system prompt. Hermes reads
               files directly from disk rather than accepting base64 payloads.
            3. manifold discovery — enumerates Hermes sub-models via /v1/models and
               strips the ``pipe_id.`` namespace prefix from sub-model IDs before
               dispatching requests (e.g. ``hermes_agent.profile`` → ``profile``).
            4. session-header gate — conditionally sends ``X-Hermes-Session-Id`` only
               when ``hermes_api_key`` is configured; Hermes returns 403 on
               unauthenticated session-continuation requests.
        """
        # [HERMES-HOOK-IDENTITY-PIPE-BEGIN]
        # resolve_hermes_identity is async (Groups.get_groups_by_member_id is async in v0.9.1)
        identity = await resolve_hermes_identity(__user__)
        # [HERMES-HOOK-IDENTITY-PIPE-END]

        url = self.valves.hermes_api_url.rstrip('/')
        headers = {
            'Content-Type': 'application/json',
            **self._auth_headers(),
        }
        # [HERMES-HOOK-IDENTITY-PIPE-BEGIN]
        if identity is not None:
            headers['X-Hermes-User-Id'] = identity['user_id']
            headers['X-Hermes-Tenant-Id'] = identity['tenant_id']
        # [HERMES-HOOK-IDENTITY-PIPE-END]

        # Hermes only accepts X-Hermes-Session-Id on authenticated requests.
        session_continuity_enabled = self._maybe_add_session_header(headers, __chat_id__)

        # Inject uploaded file paths into system prompt so hermes can
        # read them from the shared filesystem.
        messages = list(body.get('messages', []))
        file_context = await self._resolve_file_paths(__files__)
        if file_context:
            messages = self._inject_file_context(messages, file_context)

        # Resolve sub-model ID (strip pipe prefix: "hermes_agent.profile" → "profile")
        model_id = body.get('model', '')
        if '.' in model_id:
            _, model_id = model_id.split('.', 1)

        payload = {
            'model': model_id,
            'messages': messages,
            'stream': True,
        }
        # Pass through optional params
        for key in ('temperature', 'max_tokens', 'top_p', 'frequency_penalty', 'presence_penalty'):
            if key in body:
                payload[key] = body[key]

        # Emit start status
        await self._emit_status(
            __event_emitter__,
            'start',
            'Hermes Agent is working...',
            skill_name='Hermes Agent',
        )

        try:
            timeout = httpx.Timeout(
                connect=10.0,
                read=None,  # No read timeout — hermes sends keepalives
                write=10.0,
                pool=10.0,
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    'POST',
                    f'{url}/v1/chat/completions',
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        error_msg = f'Hermes returned HTTP {response.status_code}'
                        try:
                            error_msg = json.loads(error_body).get('error', {}).get('message', error_msg)
                        except Exception:
                            pass
                        if response.status_code == 403 and session_continuity_enabled:
                            error_msg = (
                                f'{error_msg}. Hermes session continuity requires API_SERVER_KEY on the '
                                'Hermes server and a matching hermes_api_key in this pipe.'
                            )
                        await self._emit_status(__event_emitter__, 'error', error_msg, done=True)
                        yield {'error': {'detail': error_msg}}
                        return

                    # Parse mixed SSE stream
                    current_event_type = None
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()

                        # Empty line = SSE event boundary
                        if not line:
                            current_event_type = None
                            continue

                        # Keepalive comment
                        if line.startswith(':'):
                            continue

                        # Custom event type header
                        if line.startswith('event:'):
                            current_event_type = line[len('event:') :].strip()
                            continue

                        # Data line
                        if line.startswith('data:'):
                            data_str = line[len('data:') :].strip()

                            # Stream terminator
                            if data_str == '[DONE]':
                                yield 'data: [DONE]'
                                break

                            # Hermes tool progress event
                            if current_event_type == 'hermes.tool.progress':
                                try:
                                    payload_data = json.loads(data_str)
                                    await self._emit_tool_progress(__event_emitter__, payload_data)
                                except json.JSONDecodeError:
                                    log.warning(f'Bad tool progress JSON: {data_str}')
                                current_event_type = None
                                continue

                            # [HERMES-HOOK-MEMORY-RECALL-PIPE-BEGIN]
                            # Hermes memory recall event — emitted once per turn when
                            # the memory provider returned non-empty prefetch context.
                            if current_event_type == 'hermes.memory.recalled':
                                try:
                                    payload_data = json.loads(data_str)
                                    await self._emit_memory_recall(__event_emitter__, payload_data)
                                except json.JSONDecodeError:
                                    log.warning(f'Bad memory recall JSON: {data_str}')
                                current_event_type = None
                                continue
                            # [HERMES-HOOK-MEMORY-RECALL-PIPE-END]

                            # [HERMES-HOOK-CONTINUATION-PIPE-BEGIN]
                            # Hermes proactive continuation event — emitted at most
                            # once per session by run_agent's continuation probe when
                            # a reasoning-capable provider reports an incomplete task.
                            if current_event_type == 'hermes.continuation.suggested':
                                try:
                                    payload_data = json.loads(data_str)
                                    await self._emit_continuation(__event_emitter__, payload_data)
                                except json.JSONDecodeError:
                                    log.warning(f'Bad continuation JSON: {data_str}')
                                current_event_type = None
                                continue
                            # [HERMES-HOOK-CONTINUATION-PIPE-END]

                            # Standard OpenAI chunk — yield as dict for process_line
                            try:
                                chunk = json.loads(data_str)
                                yield chunk
                            except json.JSONDecodeError:
                                log.warning(f'Bad SSE JSON: {data_str}')

                            current_event_type = None

            # Stream completed normally
            await self._emit_status(__event_emitter__, 'complete', 'Hermes Agent completed', done=True)

        except httpx.ConnectError as e:
            msg = f'Cannot connect to Hermes at {url}: {e}'
            log.error(msg)
            await self._emit_status(__event_emitter__, 'error', msg, done=True)
            yield {'error': {'detail': msg}}

        except httpx.ReadTimeout as e:
            msg = f'Hermes read timeout: {e}'
            log.error(msg)
            await self._emit_status(__event_emitter__, 'error', msg, done=True)
            yield {'error': {'detail': msg}}

        except Exception as e:
            msg = f'Hermes pipe error: {e}'
            log.exception(msg)
            await self._emit_status(__event_emitter__, 'error', msg, done=True)
            yield {'error': {'detail': msg}}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        if self.valves.hermes_api_key:
            return {'Authorization': f'Bearer {self.valves.hermes_api_key}'}
        return {}

    def _maybe_add_session_header(self, headers: dict, chat_id: str | None) -> bool:
        """Add Hermes session header only when auth-backed continuation is possible."""
        if not chat_id:
            return False
        if not self.valves.hermes_api_key:
            log.debug(
                'Skipping X-Hermes-Session-Id for chat %s because hermes_api_key is not configured; '
                'requests will rely on stateless request-body history.',
                chat_id,
            )
            return False

        headers['X-Hermes-Session-Id'] = str(chat_id)
        return True

    async def _resolve_file_paths(self, files: list | None) -> str:
        """Resolve Open WebUI file IDs to filesystem paths."""
        if not files:
            return ''

        lines = []
        try:
            from open_webui.models.files import Files
            from open_webui.config import UPLOAD_DIR
            import os

            for f in files:
                fid = f.get('id') if isinstance(f, dict) else None
                if not fid:
                    continue
                record = await Files.get_file_by_id(fid)
                if not record:
                    continue
                abs_path = os.path.join(UPLOAD_DIR, record.path)
                if os.path.exists(abs_path):
                    lines.append(f'- {abs_path} ({record.filename})')
        except Exception as e:
            log.warning(f'File resolution failed: {e}')

        return '\n'.join(lines)

    @staticmethod
    def _inject_file_context(messages: list, file_context: str) -> list:
        """Prepend file paths to the system prompt."""
        file_block = (
            '\n\n[Shared filesystem — files uploaded by the user]\n'
            f'{file_context}\n'
            'You can read these files directly from the paths above.'
        )
        # Find existing system message or create one
        if messages and messages[0].get('role') == 'system':
            messages = [dict(messages[0]), *messages[1:]]
            messages[0]['content'] = messages[0].get('content', '') + file_block
        else:
            messages = [{'role': 'system', 'content': file_block.strip()}, *messages]
        return messages

    @staticmethod
    async def _emit_status(emitter, sub_action: str, description: str, done: bool = False, **extra):
        if not emitter:
            return
        try:
            await emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': _STATUS_ACTION,
                        'sub_action': sub_action,
                        'description': description,
                        'done': done,
                        **extra,
                    },
                }
            )
        except Exception as e:
            log.debug(f'Event emit failed: {e}')

    @staticmethod
    async def _emit_tool_progress(emitter, payload: dict):
        """Translate hermes.tool.progress into Open WebUI status event."""
        if not emitter:
            return
        tool = payload.get('tool', 'unknown')
        emoji = payload.get('emoji', '')
        label = payload.get('label', '')
        desc = f'{emoji} {tool}: {label}'.strip() if label else f'{emoji} {tool}'.strip()
        try:
            await emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': _STATUS_ACTION,
                        'sub_action': 'tool_use',
                        'tool_name': tool,
                        'tool_input': label,
                        'description': desc,
                        'done': False,
                    },
                }
            )
        except Exception as e:
            log.debug(f'Tool progress emit failed: {e}')

    @staticmethod
    async def _emit_memory_recall(emitter, payload: dict):
        """Translate hermes.memory.recalled into Open WebUI status event.

        Called at most once per turn, before any content chunks arrive.
        Forwards recalled_facts from the hermes SSE payload so the frontend
        chip can show provenance and offer per-fact forget actions.

        Each entry in recalled_facts must conform to:
            {'id': str, 'content_preview': str, 'score': float | None}
        Partial / malformed entries are sanitised defensively — missing keys
        become safe defaults; content_preview is truncated to 200 chars.
        """
        if not emitter:
            return

        _PREVIEW_LIMIT = 200

        raw_facts = payload.get('recalled_facts', [])
        recalled_facts: list[dict] = []
        if isinstance(raw_facts, list):
            for entry in raw_facts:
                if not isinstance(entry, dict):
                    continue
                preview = str(entry.get('content_preview', ''))
                if len(preview) > _PREVIEW_LIMIT:
                    preview = preview[:_PREVIEW_LIMIT]
                score = entry.get('score')
                try:
                    score = float(score) if score is not None else None
                except (TypeError, ValueError):
                    score = None
                recalled_facts.append(
                    {
                        'id': str(entry.get('id', '')),
                        'content_preview': preview,
                        'score': score,
                    }
                )

        try:
            await emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': 'hermes_memory_recall',
                        'provider': payload.get('provider', 'unknown'),
                        'context_preview': payload.get('context_preview', ''),
                        'context_token_estimate': payload.get('context_token_estimate', 0),
                        'recalled_facts': recalled_facts,
                        'done': False,
                    },
                }
            )
        except Exception as e:
            log.debug(f'Memory recall emit failed: {e}')

    # [HERMES-HOOK-CONTINUATION-PIPE-BEGIN]
    @staticmethod
    async def _emit_continuation(emitter, payload: dict):
        """Translate hermes.continuation.suggested into Open WebUI status event.

        Emitted at most once per session when the hermes-side continuation
        probe finds an incomplete task in the user's prior history. Frontend
        surfaces this as a ContinueCard via action="hermes_continuation".
        """
        if not emitter:
            return
        try:
            await emitter(
                {
                    'type': 'status',
                    'data': {
                        'action': 'hermes_continuation',
                        'task_summary': payload.get('task_summary', ''),
                        'confidence': payload.get('confidence', 'low'),
                        'last_session_age_hours': payload.get('last_session_age_hours'),
                        'done': False,
                    },
                }
            )
        except Exception as e:
            log.debug(f'Continuation emit failed: {e}')

    # [HERMES-HOOK-CONTINUATION-PIPE-END]

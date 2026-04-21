# Wave 3 - Reveal Packet

**Wave Objective**: Expose hermes's memory provider tools (`fact_store`, `fact_feedback`, etc.) through a minimal authenticated HTTP endpoint on hermes, and proxy through an openwebui router scoped to the authenticated user's identity. UI (ProfilePanel + /settings/memory route + Playwright e2e) deferred to W3b continuation.
**Data-flow segment**: output (admin / inspection)
**Blast radius**: medium (new endpoint on hermes, new router on openwebui)
**Total waves**: 3 of 4

## Architectural decision (from memo I-3 T0 triage resolved by user 2026-04-21)

hermes exposes ONLY OpenAI-compatible `/v1/chat/completions` + `/v1/responses` + `/api/jobs/*` + health routes. No direct tool-call HTTP surface exists. We chose option (a): add a minimal `/v1/memory/tool` endpoint on hermes that dispatches to the existing `memory_manager.handle_tool_call(tool_name, args, **kwargs)` at `agent/memory_manager.py:249`.

Rationale: smaller surface change than embedding synthetic tool calls in chat completions; keeps LLM out of the admin path; additive + marker-bracketed so rebase-friendly; reuses the existing `handle_tool_call` plumbing.

## Scope (backend only — UI carried to W3b)

### Hermes side

- `gateway/platforms/api_server.py` — MODIFY, marker-bracketed
  - New handler `_handle_memory_tool(request)`. Authenticated via existing bearer-token middleware. Accepts JSON:
    ```json
    {"tool_name": "fact_store", "args": {"action": "list", "limit": 50}, "user_id": "alice", "tenant_id": "acme"}
    ```
  - Dispatches `memory_manager.handle_tool_call(tool_name, args, user_id=..., tenant_id=...)` — same kwarg contract as W1 AIAgent→initialize_all chain. Falsy user_id/tenant_id omitted from kwargs, same "empty-as-absent" semantics.
  - Returns `{"result": <provider response>}` on success, OpenAI-style error shape on failure.
  - Registered at `POST /v1/memory/tool`.
  - Marker: `HERMES-HOOK-MEMORY-TOOL-ENDPOINT`

### Openwebui side

- `backend/open_webui/routers/hermes_memory.py` — NEW
  - Proxies authenticated requests to hermes's `/v1/memory/tool`.
  - Endpoints:
    - `GET /api/v1/hermes/memory/profile` → hermes `fact_store(action=list, category=user_pref)` scoped to `current_user`.
    - `POST /api/v1/hermes/memory/profile` → hermes `fact_store(action=add, content=...)`.
    - `DELETE /api/v1/hermes/memory/profile/<fact_id>` → hermes `fact_store(action=remove, fact_id=...)`.
    - `GET /api/v1/hermes/memory/search?q=...` → hermes `fact_store(action=search, query=...)`.
  - ALL endpoints MUST use `current_user.id` + `resolve_hermes_identity(current_user)` for scoping — request body user_id is ignored (IDOR defence).
  - Uses httpx client with `HERMES_API_URL` env var (same as pipe).
- `backend/open_webui/main.py` — MODIFY, marker-bracketed
  - One marker pair registering the new router.
  - Marker: `HERMES-HOOK-MEMORY-ROUTER-REGISTER`

### Behavioural tests

- hermes: `tests/gateway/test_memory_tool_endpoint.py` (new)
  - POST `/v1/memory/tool` with `fact_store add` → provider responds; POST with list → shows the added fact.
  - Authorization required (existing bearer middleware).
  - user_id / tenant_id kwargs reach `handle_tool_call` (spy probe).
- openwebui: `backend/open_webui/test/routers/test_hermes_memory_router.py` (new)
  - In-process FastAPI TestClient tests for each endpoint.
  - Mock hermes `httpx` responses.
  - Assert `current_user.id` is used as scoping kwarg (not request-body user_id — IDOR defence).

## Out of scope (W3b continuation)

- `src/lib/components/hermes/ProfilePanel.svelte` — new Svelte component
- `src/routes/(app)/settings/memory/+page.svelte` — route + form wiring
- `e2e/tests/hermes-profile-panel.spec.ts` — Playwright behavioural test

## Constraints

Allowed files (exhaustive):
- hermes-agent: `gateway/platforms/api_server.py`, `tests/gateway/test_memory_tool_endpoint.py` (new)
- openwebui: `backend/open_webui/routers/hermes_memory.py` (new), `backend/open_webui/main.py` (marker-bracketed edit), `backend/open_webui/test/routers/test_hermes_memory_router.py` (new), `docs/hermes-rebase-patchmap.md` (add 2 rows)

Forbidden:
- W1/W2/W2b markers frozen. All files they touched are READ-ONLY this wave.
- `plugins/memory/*/`, `agent/memory_manager.py` — the ABC is read-only.
- Any `src/` file (UI deferred).

## Success Criteria (descoped for boost)

1. hermes POST `/v1/memory/tool` dispatches to `memory_manager.handle_tool_call` with correct user_id/tenant_id kwargs.
2. openwebui `/api/v1/hermes/memory/*` endpoints call hermes correctly, scoped by `current_user.id`.
3. Cross-user scoping verified: user A's request gets A's facts, not B's. Even if the request body tries to pass `user_id=B`, the router IGNORES body and uses `current_user`.
4. 2 new marker pairs registered, verify_hermes_hooks.sh exits 0 (total 9 markers).
5. In-process behavioural probes pass on both sides.

## Turn Plan (compressed)

- T1: hermes endpoint + openwebui router + main.py registration + both behavioural probes. Single turn, both repos.
- No T2/T3 this wave — close at T1 PASS. UI work is explicit W3b carry at wave-reveal time.

## Handoff from Wave 2

W2 T1 + W2b closed. Callback-based plumbing established for memory-recall signalling. W3 builds on the same `memory_manager` contract (line 249 `handle_tool_call` is vendor-stable).

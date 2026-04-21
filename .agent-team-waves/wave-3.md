# Wave 3 - Retrospective

**Status**: COMPLETE_WITH_CARRY
**Wave Objective**: Expose hermes memory provider tools through an authenticated HTTP endpoint + proxy through openwebui router scoped to `current_user`, IDOR-safe. UI deferred to W3b.
**Turns executed**: 1 (T1 only — explicit boost-mode descope pattern matching W2→W2b split)
**Date**: 2026-04-21
**Commits**:
- openwebui `f5d8fb7ab` on `feat/v0.8.12-hermes-port`
- hermes-agent `7044658b` on `feat/kuleon-openwebui-identity`

## Architectural decision taken at T0

hermes exposes ONLY OpenAI-compat `/v1/chat/completions` + `/v1/responses` + `/api/jobs/*` + health. No direct tool-call HTTP surface existed. Option (a) chosen: add minimal `/v1/memory/tool` endpoint dispatching to the existing `memory_manager.handle_tool_call` at `agent/memory_manager.py:249`. Rationale: smaller surface change than synthetic tool-call-over-chat, LLM out of the admin path, reuses existing plumbing.

## What landed

**Hermes side** (`78dd81c0` base → `7044658b`):
- `gateway/platforms/api_server.py` `_handle_memory_tool` method + route registration at `POST /v1/memory/tool`. Lightweight MemoryManager built per-request, loads configured provider from `hermes_cli.config.load_config().memory.provider`, initialises with synthetic session_id + identity kwargs, dispatches tool call, returns parsed JSON result. Marker: `HERMES-HOOK-MEMORY-TOOL-ROUTE`.
- `tests/gateway/test_memory_tool_endpoint.py` (new, 3 probes): spy provider records `handle_tool_call` + `initialize` kwargs; verifies identity flow, falsy-elides symmetry, malformed-body 400.

**Openwebui side** (`5d41f6fc5` base → `f5d8fb7ab`):
- `backend/open_webui/routers/hermes_memory.py` (new): GET/POST/DELETE/GET endpoints proxy to hermes `/v1/memory/tool`. **Critical IDOR defence**: every endpoint resolves identity from `current_user` via `resolve_hermes_identity` (W1) — any `user_id` / `tenant_id` in the request body is IGNORED. Request path scoping is authoritative.
- `backend/open_webui/main.py` router registration. Two marker regions: import block + `app.include_router` call. Marker: `HERMES-HOOK-MEMORY-ROUTER-REGISTER`.
- `backend/open_webui/test/routers/test_hermes_memory_router.py` (new, 5 probes): GET/POST/DELETE/search all call hermes with current_user-scoped identity; spoofed-body attack verified repelled; hermes unreachable → 502 with clear detail.

## Footgun caught at marker-verify

First marker attempt used `HERMES-HOOK-MEMORY-TOOL-ENDPOINT`. The `verify_hermes_hooks.sh` regex `HERMES-HOOK-[A-Z0-9_-]+-END` matched inside `ENDPOINT-END` (the substring `-END` within `-ENDPOINT-`), producing an orphan report. Renamed to `HERMES-HOOK-MEMORY-TOOL-ROUTE`.

**Convention for future markers**: avoid `END` or `BEGIN` as substrings in logical marker-name segments. Good: `ROUTE`, `INJECT`, `GATE`, `HOOK`. Bad: `ENDPOINT`, `RENDERED`, `BEGINNING`.

## Behavioural Verifications Run

- hermes: `uv run pytest tests/gateway/test_memory_tool_endpoint.py -v` → 3/3 pass
- openwebui: `uv run pytest backend/open_webui/test/routers/test_hermes_memory_router.py -v` → 5/5 pass
- `bash scripts/verify_hermes_hooks.sh` → exit 0, 9 markers balanced
- `uv run python -c "import open_webui"` → exit 0

## What Was Given Up (carried to W3b)

- `src/routes/(app)/settings/memory/+page.svelte` — the user-facing route
- `src/lib/components/hermes/ProfilePanel.svelte` — view/edit/delete UI for peer card + conclusions
- `e2e/tests/hermes-profile-panel.spec.ts` — Playwright behavioural spec
- Frontend typed API client `src/lib/apis/hermes/memory.ts` — calls the new openwebui endpoints

Reason: pattern-consistent descope with W2→W2b. Backend contract is the load-bearing layer; UI is additive on top of a stable contract.

## Unresolved Findings

None at T1 close.

## Deferred Queue For Replanning

W3b inserted at `decomposition.md` next session. W4 remains as planned.

## Files Modified (absolute paths)

openwebui (`/Users/noelbao/Works/open-webui`, commit `f5d8fb7ab`):
- `backend/open_webui/main.py` (marker-bracketed import + registration)
- `backend/open_webui/routers/hermes_memory.py` (new, 189 lines)
- `backend/open_webui/test/routers/test_hermes_memory_router.py` (new, 194 lines)
- `docs/hermes-rebase-patchmap.md` (2 new rows)

hermes-agent (`/Users/noelbao/Works/hermes-agent`, commit `7044658b`):
- `gateway/platforms/api_server.py` (new `_handle_memory_tool` + route)
- `tests/gateway/test_memory_tool_endpoint.py` (new, 3 probes)

## Wave Summary

W3 T1 delivered the full memory-admin backend spine: hermes exposes a minimal, authenticated, provider-agnostic `POST /v1/memory/tool` that dispatches into `memory_manager.handle_tool_call` with W1-consistent identity kwargs; openwebui wraps that behind `/api/v1/hermes/memory/*` REST endpoints scoped by `current_user` with strict IDOR defence (body identity fields ignored by construction — authoritative scoping via `resolve_hermes_identity(current_user)`). Contract chain through W1/W2/W3 is now: openwebui authenticates → resolves identity → proxies to hermes admin endpoint → hermes builds MemoryManager → dispatches to provider's `handle_tool_call` with the same `user_id`/`tenant_id` kwargs honcho reads at `plugins/memory/honcho/__init__.py:293`. 9 markers registered across both forks with verify_hermes_hooks.sh invariant holding. UI (ProfilePanel + settings route + Playwright) deferred to W3b in explicit boost-mode descope pattern.

Next: W4 proactive continuation (hermes session-start hook → `hermes.continuation.suggested` SSE → openwebui pipe → ContinueCard on homepage) is the last planned wave. W3b UI + possible W2b UI polish are parallel continuation work.

# Handoff: Open WebUI x Hermes Memory UX

Date: 2026-04-24

This handoff is intentionally complete and self-contained. The next engineer should not need prior chat context.

## Objective

This fork adds a Hermes-specific integration layer to Open WebUI so Hermes can:

- remember each authenticated user's preferences and requirements accurately,
- avoid cross-user memory leakage by default,
- expose memory recall and resumable-task signals back into the Open WebUI UX.

The important product goal is not just "Open WebUI can call Hermes". The real goal is:

- user-scoped memory,
- explicit tenant semantics,
- visible memory UX,
- safe admin memory access,
- graceful continuation UX.

## Repository State

### Open WebUI

- Repo path: `/root/open-webui`
- Current branch during handoff: `main-kg`
- Current HEAD during handoff: `2490bd1a45088434b9743cad1d30576a08a54229`
- HEAD subject: `chore: remove .agent-team-waves from git tracking and add to .gitignore`
- Remotes:
  - `origin = https://github.com/galaxy-kuleon/open-webui.git`
  - `upstream = https://github.com/open-webui/open-webui.git`

### Working Tree Warning

The Open WebUI checkout is **not clean**.

Untracked files/directories observed during handoff:

- `ONBOARDING.md`
- `backend/open_webui_data.zip`
- `docker-compose.stack.override.yml`
- `e2e/test-results/`
- `screenshots/`
- `test-results/`

These look like local setup, screenshots, or test artifacts. Do not blindly include them in commits.

## Related Hermes Repo State

The paired Hermes repo is at:

- `/root/hermes-agent`

During handoff, Hermes was:

- branch: `main-kg`
- HEAD: `1ee5731cf85e07ded5adfc9df22fad7c3449eba2`
- subject: `Merge pull request #3 from galaxy-kuleon/kuleon/main-kg-honcho-memory-squash`

Hermes already contains the main server-side identity + SSE plumbing. See the companion Hermes handoff file if needed:

- `/root/hermes-agent/HANDOFF_openwebui_memory_ux.md`

This Open WebUI handoff is still written to stand alone.

## What This Fork Already Implements in Open WebUI

## 1. Hermes pipe sends authenticated user identity to Hermes

Primary file:

- `backend/open_webui/pipes/hermes_agent.py`

Implemented behavior:

- Resolves identity with `resolve_hermes_identity(__user__)`
- Sends:
  - `X-Hermes-User-Id`
  - `X-Hermes-Tenant-Id`

This is the most important customization in the OWUI->Hermes request path.

Without it, Hermes cannot distinguish one user's memory from another user's memory reliably in a shared deployment.

## 2. Tenant resolution is explicit and isolation-first

Primary file:

- `backend/open_webui/hermes/identity.py`

Current tenancy precedence:

1. Tier 1: first group with `meta.tenancy` string -> use that exact string as `tenant_id`
2. Tier 2: first group with `meta.shared_memory is True` -> use that group id as `tenant_id`
3. Tier 3: otherwise use `user_id` as `tenant_id`

This is intentional.

It prevents accidental cross-user memory leakage from generic group membership.

Important invariant:

- group membership alone must **not** imply shared memory
- shared memory must be explicit via `shared_memory: true` or explicit `tenancy`

Do not relax this unless product wants shared-memory-by-default, which would materially reduce isolation.

## 3. Hermes session continuity header is auth-gated

Primary file:

- `backend/open_webui/pipes/hermes_agent.py`

Behavior:

- `X-Hermes-Session-Id` is only sent when `hermes_api_key` exists
- if there is no Hermes API key configured, OWUI stays stateless and relies on request-body history

Why this exists:

- Hermes only allows session continuation on authenticated requests
- otherwise Hermes returns 403 for that path

This is correct and should remain.

## 4. Hermes SSE events are translated into Open WebUI status events

Primary file:

- `backend/open_webui/pipes/hermes_agent.py`

Currently handled custom Hermes SSE events:

- `hermes.tool.progress`
- `hermes.memory.recalled`
- `hermes.continuation.suggested`

Translated Open WebUI status actions:

- `hermes_memory_recall`
- `hermes_continuation`

This is the key UX bridge. It converts Hermes-side behavior into frontend-visible UI state.

## 5. Memory recall chip UI already exists

Primary files:

- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`

What already works:

- chip rendering via `action === 'hermes_memory_recall'`
- provider label
- token estimate label
- context preview fallback
- provenance list support via `recalled_facts`
- per-fact forget button via OWUI memory router

The UI is already there. Some richer payloads are waiting on Hermes side.

## 6. Continuation card UI already exists

Primary files:

- `src/lib/components/chat/HermesContinuationCard.svelte`
- `src/lib/components/chat/Chat.svelte`
- `src/lib/apis/hermes/continuation.ts`

Current behavior:

- frontend probes `/api/v1/hermes/continuation/probe` on chat mount
- also deduplicates against any SSE-driven continuation card already rendered that turn
- renders a continuation card with resume and dismiss actions

So the UI and frontend client already exist.

## 7. OWUI memory admin router already exists and is user-scoped

Primary file:

- `backend/open_webui/routers/hermes_memory.py`

Implemented routes:

- `GET /api/v1/hermes/memory/profile`
- `POST /api/v1/hermes/memory/profile`
- `DELETE /api/v1/hermes/memory/profile/{fact_id}`
- `GET /api/v1/hermes/memory/search`

Important security invariant:

- router always derives identity from authenticated `current_user`
- router does **not** trust client-supplied `user_id` / `tenant_id`

This is the IDOR defense and must stay.

## 8. OWUI continuation router already exists, but is currently a graceful stub client

Primary file:

- `backend/open_webui/routers/hermes_continuation.py`

Current behavior:

- calls Hermes `POST /v1/continuation/probe`
- but falls back to `{suggested: false}` on:
  - 404
  - connection errors
  - timeout
  - any unexpected error

That fallback is intentional because Hermes did not yet expose that HTTP endpoint at handoff time.

## Files Most Relevant to This Integration

### Backend request/stream path

- `backend/open_webui/pipes/hermes_agent.py`

### Identity / tenancy rules

- `backend/open_webui/hermes/identity.py`

### Memory admin API

- `backend/open_webui/routers/hermes_memory.py`

### Continuation probe API

- `backend/open_webui/routers/hermes_continuation.py`

### Frontend continuation UX

- `src/lib/components/chat/Chat.svelte`
- `src/lib/components/chat/HermesContinuationCard.svelte`
- `src/lib/apis/hermes/continuation.ts`

### Frontend memory recall UX

- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte`

## Test Coverage Already Present

### Identity

- `backend/open_webui/test/hermes/test_identity.py`
- `backend/open_webui/test/pipes/test_hermes_agent_headers.py`

### Memory recall SSE translation

- `backend/open_webui/test/pipes/test_hermes_memory_recall.py`
- `backend/open_webui/test/pipes/test_hermes_memory_recall_facts.py`

### Continuation SSE translation

- `backend/open_webui/test/pipes/test_hermes_continuation.py`

### Memory router security / behavior

- `backend/open_webui/test/routers/test_hermes_memory_router.py`

### Continuation router behavior

- `backend/open_webui/test/routers/test_hermes_continuation_router.py`

### Live UX artifact

- `e2e/tests/hermes-memory-chip.spec.ts`

The e2e spec documents expected UX but depends on a live stack and may not be part of normal CI.

## What Is Still Missing / Incomplete

These are the most important remaining gaps.

## Gap 1: Hermes does not yet expose `POST /v1/continuation/probe`

This is the biggest blocker to fully completing the proactive continuation UX.

OWUI side is ready:

- backend router exists
- frontend API client exists
- UI card exists
- graceful fallback exists

But Hermes side still needs the actual HTTP endpoint.

Current effect:

- SSE-based continuation works during active chat turns when Hermes emits it
- proactive continuation on chat mount remains mostly a no-op because Hermes returns 404 / fallback

## Gap 2: Hermes memory recall payload does not currently include `recalled_facts`

OWUI already supports a richer payload in `_emit_memory_recall(...)` and in `HermesMemoryRecallStatus.svelte`.

Expected richer payload shape:

```json
{
  "provider": "honcho",
  "context_preview": "...",
  "context_token_estimate": 42,
  "recalled_facts": [
    {
      "id": "42",
      "content_preview": "user prefers dark mode",
      "score": 0.88
    }
  ]
}
```

Current Hermes behavior only reliably sends:

- `provider`
- `context_preview`
- `context_token_estimate`

Effect:

- memory chip can render,
- but provenance list and per-fact forget UX are not being fully exercised.

## Gap 3: Provider label can be misleading

The current Hermes memory recall SSE path was observed emitting a hardcoded `provider` label of `holographic`.

If runtime is actually on Honcho, the Open WebUI chip becomes misleading.

## Gap 4: Frontend dedup / local state logic is functional but not ideal

### 4a. Continuation card dedup relies on DOM query

File:

- `src/lib/components/chat/Chat.svelte`

Current approach:

- checks for existing `[data-testid="hermes-continuation-card"]` in DOM

This works, but is brittle compared with explicit store/state coordination.

### 4b. Memory recall local state sync deserves a quick review

File:

- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`

Current code keeps a local mutable `localFacts` array to support instant forget UX.

This is good, but should be reviewed when changing parent status-update behavior so optimistic deletion does not get overwritten by fresh props unexpectedly.

## Recommended Next Work (In Order)

## Priority 1: Add Hermes `POST /v1/continuation/probe`

Why first:

- OWUI side is already complete enough to consume it immediately.
- This unlocks proactive continuation on chat mount, not just per-turn SSE during active chats.

Expected Hermes request contract from OWUI:

```json
{
  "user_id": "alice",
  "tenant_id": "acme-corp"
}
```

Expected response contract to OWUI:

```json
{
  "suggested": true,
  "task_summary": "You were porting skip_rag.py to the new ABC.",
  "confidence": "low",
  "last_session_age_hours": 26
}
```

If Hermes adds this endpoint, OWUI likely does not need major code changes.

## Priority 2: Have Hermes emit `recalled_facts`

Why second:

- OWUI UI already supports provenance and forget controls.
- This makes the memory UX more transparent and useful.

If Hermes cannot always provide provenance, it should still send:

```json
"recalled_facts": []
```

instead of omitting the key.

## Priority 3: Ensure provider label reflects actual runtime provider

Once Hermes fixes this, OWUI chip text becomes trustworthy in mixed memory-provider environments.

## Priority 4: Optional OWUI polish after Hermes catches up

- Replace DOM-based continuation dedup with explicit state/store logic.
- Consider persisting continuation dismiss state for a session.
- Revisit memory chip local-state handling if live event frequency changes.

## Validation Commands

## Open WebUI backend tests

Recommended targeted validation after any OWUI-side change:

```bash
cd /root/open-webui
python -m pytest \
  backend/open_webui/test/hermes/test_identity.py \
  backend/open_webui/test/pipes/test_hermes_agent_headers.py \
  backend/open_webui/test/pipes/test_hermes_memory_recall.py \
  backend/open_webui/test/pipes/test_hermes_memory_recall_facts.py \
  backend/open_webui/test/pipes/test_hermes_continuation.py \
  backend/open_webui/test/routers/test_hermes_memory_router.py \
  backend/open_webui/test/routers/test_hermes_continuation_router.py
```

## Frontend typecheck

```bash
cd /root/open-webui
npm run check
```

## Frontend e2e when live stack is available

There is already a behavioral spec for the memory chip:

```bash
cd /root/open-webui
npx playwright test e2e/tests/hermes-memory-chip.spec.ts
```

Run only when:

- Open WebUI is running
- Hermes is reachable
- Hermes has a configured provider/model
- memory provider can actually return non-empty recall for the test user

## Important Invariants To Preserve

1. Identity must remain server-derived.
   - Do not trust client body fields for `user_id` or `tenant_id`.

2. Per-user isolation must remain the default.
   - Do not reintroduce implicit group-shared memory.

3. Session continuation must remain auth-gated.
   - Do not start sending `X-Hermes-Session-Id` without Hermes auth.

4. Hermes custom SSE events must not leak into normal assistant transcript text.
   - They are UX events, not message content.

5. Open WebUI's fork-specific logic is product logic, not just incidental glue.
   - It exists specifically to make Hermes memory accurate per user.

## Practical Summary

If the next engineer only remembers a few things, remember these:

1. Open WebUI is already correctly sending Hermes `user_id` and `tenant_id` headers.
2. The tenancy resolver is intentionally conservative to prevent cross-user memory leakage.
3. The memory router is already protected against IDOR by deriving identity from authenticated user state.
4. The memory chip and continuation card are already implemented on the OWUI side.
5. The biggest remaining blocker is on Hermes, not OWUI:
   - `POST /v1/continuation/probe`
   - richer `recalled_facts` payload

That is where the highest-value next work is.

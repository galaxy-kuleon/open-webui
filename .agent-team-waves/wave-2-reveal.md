# Wave 2 - Reveal Packet

**Wave Objective**: Surface a first visible "it remembers me" signal — when hermes's memory provider injects recalled context into the turn, emit a dedicated SSE event that openwebui pipe translates into a frontend-addressable event, so a UI component can render a chip on the assistant message.
**Data-flow segment**: transform (hermes SSE emit → pipe translate → UI render)
**Blast radius**: medium (new SSE event type on hermes side, pipe event-type branch, minimal frontend)
**Total waves**: 2 of 4

## Budget reality

User stated ~1h left mid-W1; remaining ~25min at W2 start. W2 is compressed into **T1 backend-only** (SSE + pipe) with Playwright UI deferred to a continuation wave. This is an explicit SCOPE DESCOPE at wave-reveal time, not a FAIL-driven descope.

## Spec Slice (from memo I-2, descoped)

### I-2 (W2) — Memory recall SSE event + pipe translation (UI deferred)

**Severity**: feature (first visible wow moment — partial landing this wave: event plumbing only)
**Blast radius**: medium (no UI this wave)

**Files in scope (backend-only T1)**:

hermes-agent side:
- `run_agent.py` or the SSE emission site inside the agent loop — MODIFY, marker-bracketed
  - When honcho/holographic provider's `prefetch(query, session_id=...)` returns non-empty text (indicating memory context is about to be injected), emit `event: hermes.memory.recalled\ndata: {"provider": "<name>", "context_preview": "<first 200 chars>", "context_token_estimate": <int>}`
  - Emission happens once per turn at turn start before any `data:` content frames
  - If prefetch returns empty, no event (absence = no recall this turn)
  - Marker: `HERMES-HOOK-MEMORY-RECALL-SSE`

openwebui side:
- `backend/open_webui/pipes/hermes_agent.py` — MODIFY, marker-bracketed (third marker pair on this file)
  - Parallel to the existing `hermes.tool.progress` handler (line ~220), add `hermes.memory.recalled` handling
  - Translate to `__event_emitter__` with `action="hermes_memory_recall"` payload `{provider, context_preview, context_token_estimate}`
  - Distinct action name so future UI can discriminate from tool progress
  - Marker: `HERMES-HOOK-MEMORY-RECALL-PIPE`

**Behavioural verification (per user directive, no pytest baseline)**:
- hermes side: write/run an in-process probe that invokes the SSE emission path with a mock provider whose `prefetch()` returns canned text; capture raw stream output; assert it contains `event: hermes.memory.recalled` before the first `data:` chunk, with expected payload shape.
- openwebui side: write/run an in-process pipe probe that feeds a mock SSE stream containing `hermes.memory.recalled` into the pipe; capture `__event_emitter__` calls; assert one call with `action="hermes_memory_recall"`.

**Out of scope this wave**:
- Frontend Svelte `MemoryChip.svelte`
- Message-render slot injection
- Playwright e2e
- These land in a continuation wave (call it W2b) when time permits

## Constraints for This Wave

**Allowed files to modify**:
hermes-agent:
- `run_agent.py` (new marker pair)
- `tests/` — one new behavioural probe file

openwebui:
- `backend/open_webui/pipes/hermes_agent.py` (third marker pair — additive branch in SSE parser)
- `backend/open_webui/test/pipes/` — one new behavioural probe file

shared:
- `docs/hermes-rebase-patchmap.md` (add 2 new markers)

**Forbidden** (identical to W1 forbidden list; plus W1-committed files which are now frozen):
- `backend/open_webui/hermes/identity.py` (W1 closed — frozen unless explicitly revisited)
- `hermes-agent/gateway/platforms/api_server.py` (W1 closed)
- `plugins/memory/*/`, `agent/memory_manager.py`, `agent/memory_provider.py`
- All `src/` (UI deferred)

## Handoff from Wave 1

W1 closed COMPLETE 2026-04-21. 4 markers registered, `verify_hermes_hooks.sh` exits 0. Identity chain verified end-to-end via Tier A probe.

Key artefact reusable in W2: the MemoryManager / MemoryProvider contract is unchanged — W2 hooks into the provider's existing `prefetch()` return value, not into memory-manager internals.

## Success Criteria (descoped for boost mode)

1. `hermes.memory.recalled` SSE event emitted with correct shape when provider.prefetch returns non-empty.
2. Pipe translates to `__event_emitter__` call with `action="hermes_memory_recall"`.
3. 2 new marker pairs registered in patchmap.
4. `bash scripts/verify_hermes_hooks.sh` exits 0 with 6 markers now (4 from W1 + 2 new).
5. In-process behavioural probes on both sides pass.
6. No `utils/middleware.py`-style churn.

## Turn Plan (compressed — aiming T1 only, T2/T3 if time)

- **T1**: hermes SSE emission + openwebui pipe translation + in-process probes both sides. Single turn does both repos because the contract is tight and the edits are small. Revisit: none (parallel to T1 work — new contract).
- **T2 (if time)**: global-consistency cross-validation + wave close (patchmap + verify + commits). Revisit T1 with global-consistency.

If user time runs out after T1, commit T1's work as W2 partial and mark wave `COMPLETE_WITH_CARRY` with UI items carried to a named W2b.

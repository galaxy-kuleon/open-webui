# Wave 4 - Reveal Packet (PLANNED, not yet executed)

**Wave Objective**: When a new chat session starts for a known user, detect whether they left an unfinished task in a prior session; if so, surface a proactive "continue where you left off" suggestion in the chat UI.
**Data-flow segment**: transform (hermes session-start hook → SSE → pipe → UI)
**Blast radius**: medium-high (hermes hook + SSE + pipe branch + new frontend slot)
**Total waves**: 4 of 4 (final planned wave)

## Precondition for W4 execution

**Active memory provider MUST expose a dialectic-reasoning-style tool** — e.g. honcho's `honcho_reasoning`. The holographic provider (W1/W2/W3 active provider) does NOT — it has `fact_store` but no LLM-backed "reason about user's last session" capability.

Therefore W4 cannot be executed end-to-end until the provider-upgrade wave (memo option P1 honcho self-host, or an extension of holographic that adds a reasoning tool) has landed.

## Scope (when executed)

### Hermes side

- `run_agent.py` `AIAgent.__init__` — MODIFY, marker-bracketed
  - After `memory_manager.initialize_all(...)`, check whether any registered provider exposes a reasoning tool (scan `provider.get_tool_schemas()` for name matching `*_reasoning` or `honcho_reasoning`).
  - If yes AND this looks like a session-start (new chat_id, last session for this peer > 1 hour old — provider-specific heuristic), fire `memory_manager.handle_tool_call("honcho_reasoning", {"query": "Did the user leave any task incomplete in their last session? If yes, summarise in one sentence. Otherwise respond with [NONE].", "reasoning_level": "low"})` in a background thread with bounded timeout (~8s).
  - If the response is not `[NONE]` and non-empty, invoke `self.continuation_callback(task_summary=..., last_session_age_hours=..., confidence=...)`.
  - New kwarg `continuation_callback: Optional[Callable] = None` on `AIAgent.__init__`.
  - Marker: `HERMES-HOOK-CONTINUATION-HOOK`.

- `gateway/platforms/api_server.py` — MODIFY, marker-bracketed
  - Parallel to the W2 memory-recall plumbing:
    - `_handle_chat_completions` builds `_on_continuation(task_summary, **kwargs)` closure, enqueues `__continuation__` tag on the stream queue.
    - `_emit` closure translates `__continuation__` into `event: hermes.continuation.suggested\ndata: {"task_summary": "...", "last_session_age_hours": ..., "confidence": ...}` SSE frame.
    - `continuation_callback` threaded through `_run_agent` → `_create_agent` → `AIAgent`.
  - Marker: `HERMES-HOOK-CONTINUATION-SSE`.

### Openwebui side

- `backend/open_webui/pipes/hermes_agent.py` — MODIFY, marker-bracketed
  - Fourth SSE branch handler (parallel to existing `hermes.tool.progress`, `hermes.memory.recalled`): translate `event: hermes.continuation.suggested` to `__event_emitter__` with `action="hermes_continuation"` and the full payload.
  - Marker: `HERMES-HOOK-CONTINUATION-PIPE`.

- (W4b — UI, deferred): `src/lib/components/hermes/ContinueCard.svelte`, `src/routes/(app)/+page.svelte` homepage slot, `src/lib/components/hermes/ContinuationChip.svelte` for mid-chat display, Svelte store for dismissal state.

### Behavioural verification (when executed)

- Hermes: spy provider with `honcho_reasoning` tool schema returning a canned "user was porting module X" string → SSE stream contains `event: hermes.continuation.suggested` with that task_summary. Spy returning `[NONE]` → no event emitted. Spy without the tool schema at all → no event emitted (capability-gated).
- Openwebui pipe: in-process probe feeds mock SSE stream → assert `__event_emitter__` called with `action="hermes_continuation"`.

## Out of scope (carried to W4b)

- Frontend `ContinueCard.svelte`, homepage slot, `ContinuationChip.svelte`.
- Playwright e2e requiring multi-session state: (1) session 1 establishes a task, (2) close, (3) simulate age gap, (4) session 2 shows the card.
- This is substantial UX work requiring live stack + memory provider that scopes per-user AND has reasoning.

## Constraints

Allowed files (when executed):
- hermes-agent: `run_agent.py` (new marker pair), `gateway/platforms/api_server.py` (new marker pair), `tests/gateway/test_continuation_sse.py` (new)
- openwebui: `backend/open_webui/pipes/hermes_agent.py` (fourth marker pair), `backend/open_webui/test/pipes/test_hermes_continuation.py` (new)
- shared: `docs/hermes-rebase-patchmap.md` (3 new rows)

Forbidden (same as W3 plus W3-committed files now frozen):
- All prior-wave markers frozen.
- `plugins/memory/*/`, `agent/memory_manager.py`, `agent/memory_provider.py` — vendor-opaque.
- All `src/` (UI is W4b).

## Success Criteria (when executed)

1. Hermes emits `hermes.memory.suggested` SSE event iff provider has reasoning tool AND tool returns non-`[NONE]`.
2. Capability gating works: spy provider without `honcho_reasoning` → no event.
3. Pipe translates to `action="hermes_continuation"` emitter call.
4. 3 new marker pairs registered; verify_hermes_hooks.sh exits 0 with 12 markers total.
5. Behavioural probes both sides pass.

## Precondition check before W4 T1 starts

1. `hermes memory status` → Provider has reasoning capability (honcho, or hypothetical extended-holographic).
2. Either honcho self-hosted + 3 LLM API keys configured (memo option P1), OR holographic has been extended with a reasoning tool (memo option P2's sibling).

If neither precondition is met, W4 execution is blocked. The plumbing is ready in-memo to be activated when the provider upgrade lands.

## Handoff from Wave 3

W3 T1 closed. 9 markers registered. Memory-admin REST surface available at `/api/v1/hermes/memory/*` scoped by `current_user`. UI carried to W3b. Key code reusable in W4: the callback+SSE+queue pattern from W2 maps 1:1 to W4 — copy the plumbing, swap event name and payload.

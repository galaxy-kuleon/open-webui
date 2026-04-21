# Wave 4 - Retrospective

**Status**: COMPLETE_WITH_CARRY
**Wave Objective**: Capability-gated continuation probe. When a memory provider exposes a reasoning tool, detect unfinished prior-session tasks and surface via SSE → pipe → UI.
**Turns executed**: 1 (T1 backend; UI → W4b)
**Date**: 2026-04-21
**Commits**:
- openwebui `98543949a` on `feat/v0.8.12-hermes-port`
- hermes-agent `b6f8dc50` on `feat/kuleon-openwebui-identity`

## What landed

**Hermes side**:
- `agent/_continuation_probe.py` (new, pure module, 169 lines) — `find_reasoning_tool()` + `maybe_emit_continuation()`. Sentinel-aware (`[NONE]` case-insensitive + whitespace-tolerant). Daemon-thread dispatch; `_synchronous=True` for testability.
- `run_agent.py` AIAgent.__init__: new `continuation_callback` kwarg, self-assigned, invoked after `memory_manager.initialize_all` with the identity kwargs already present. Marker: `HERMES-HOOK-CONTINUATION-HOOK`.
- `gateway/platforms/api_server.py`: `_on_continuation` closure in `_handle_chat_completions` → `__continuation__` queue tag → `_emit` translates to `event: hermes.continuation.suggested` SSE frame. Callback threaded through `_run_agent` + `_create_agent`. Marker: `HERMES-HOOK-CONTINUATION-SSE` (4 regions).
- `tests/gateway/test_continuation_probe.py` (new, 8/8 pass).

**Openwebui side**:
- `backend/open_webui/pipes/hermes_agent.py`: fourth SSE branch, `_emit_continuation` static method. Marker: `HERMES-HOOK-CONTINUATION-PIPE`.
- `backend/open_webui/test/pipes/test_hermes_continuation.py` (new, 2/2 pass).

## Capability gating verified

- Providers with `fact_store` only → `find_reasoning_tool()` returns None → `handle_tool_call` never invoked → no false event. Verified by `test_find_reasoning_tool_returns_none_for_fact_store_only` + `test_maybe_emit_continuation_skips_without_reasoning_capability`.
- Providers exposing `honcho_reasoning` or `*_reasoning` → detected → probed → callback fires on non-`[NONE]` response. Verified by `test_maybe_emit_continuation_fires_callback_on_non_sentinel`.
- Runtime config today is holographic → no reasoning tool → no events in production until provider upgrade. Plumbing is pre-validated via spy-provider probes.

## Behavioural Verifications Run

- `uv run pytest tests/gateway/test_continuation_probe.py -v` → 8/8 PASS
- `uv run pytest backend/open_webui/test/pipes/test_hermes_continuation.py -v` → 2/2 PASS
- `bash scripts/verify_hermes_hooks.sh` → exit 0, 13 markers balanced
- `uv run python -c "import open_webui"` → exit 0
- Hermes-side import sanity for `_continuation_probe`, `run_agent`, `api_server` → all clean

## What Was Given Up (W4b continuation)

- `src/lib/components/hermes/ContinueCard.svelte` — homepage slot card
- `src/lib/components/hermes/ContinuationChip.svelte` — mid-chat variant
- `src/routes/(app)/+page.svelte` marker for homepage slot
- `src/lib/hermes/continuationStore.ts` — Svelte store with dismissal state
- `e2e/tests/hermes-continuation.spec.ts` — multi-session Playwright spec

Pattern-consistent with W2→W2b and W3→W3b descope.

## Runtime activation prerequisite

W4's plumbing is LIVE but DORMANT at current runtime because active provider is `holographic` which lacks reasoning capability. Activation requires one of:

- **P1**: honcho self-host + three LLM API keys (Gemini + Anthropic + OpenAI) configured per memo amendment.
- **P2-sibling**: extend holographic with a reasoning tool (would need to be added to `plugins/memory/holographic/` — currently out of our fork scope).

When either lands, W4 fires automatically — zero further code changes needed. The spy-provider tests document what the wire shape will be.

## Unresolved Findings

None at T1 close.

## Deferred Queue For Replanning

W4b added to task list (#12). W2b/W3b already closed.

## Files Modified (absolute paths)

openwebui (`/Users/noelbao/Works/open-webui`, commit `98543949a`):
- `backend/open_webui/pipes/hermes_agent.py` (fourth marker region)
- `backend/open_webui/test/pipes/test_hermes_continuation.py` (new)
- `docs/hermes-rebase-patchmap.md` (3 new rows — 1 openwebui + 2 hermes)

hermes-agent (`/Users/noelbao/Works/hermes-agent`, commit `b6f8dc50`):
- `agent/_continuation_probe.py` (new)
- `run_agent.py` (AIAgent.__init__ + invocation block)
- `gateway/platforms/api_server.py` (4 marker regions all under HERMES-HOOK-CONTINUATION-SSE)
- `tests/gateway/test_continuation_probe.py` (new)

## Wave Summary

W4 T1 closed the planned 4-wave /atw run's backend layer. All four wire-level contracts (identity, memory-recall signal, memory-admin REST, proactive continuation) now land behind marker-bracketed hooks with behavioural probes passing on both sides. Plumbing for W4 is dormant at current runtime because the active memory provider (holographic) lacks reasoning capability, but the shape is pre-validated via spy-provider tests and will activate automatically when a reasoning-capable provider lands. 13 markers registered; verify_hermes_hooks.sh exits 0 on both forks.

Three UI carries outstanding:
- W2b ✅ closed (MemoryChip shipped)
- W3b ✅ closed (Hermes memory settings tab shipped)
- W4b 🔄 pending (ContinueCard homepage slot + Playwright e2e)

The /atw run closes here for the planned item set. W4b is the last meaningful downstream work; honcho self-host / holographic reasoning extension is the precondition that converts W4 from dormant to live.

# Wave 2 - Retrospective

**Status**: COMPLETE_WITH_CARRY
**Wave Objective**: Emit `hermes.memory.recalled` SSE event on non-empty provider prefetch; translate in openwebui pipe to frontend-addressable `__event_emitter__` status event. UI rendering deferred.
**Turns executed**: 1 (T1 only — explicit boost-mode descope at wave-reveal time, not FAIL-driven)
**Date**: 2026-04-21
**Commits**:
- openwebui `5d41f6fc5` on `feat/v0.8.12-hermes-port`
- hermes-agent `5147c070` on `feat/kuleon-openwebui-identity`

## What landed (T1)

**Hermes side** (single turn, both regions committed together):
- `run_agent.py` marker `HERMES-HOOK-MEMORY-RECALL-SSE` (9695-9703) — after `prefetch_all()`, invoke `self.memory_recall_callback` when the aggregated recall text is non-empty. Callback parameter added to `AIAgent.__init__` around line 599 and assigned around line 799 (parallel to existing `tool_progress_callback`).
- `gateway/platforms/api_server.py` marker `HERMES-HOOK-MEMORY-RECALL-SSE` (886-908) — `_on_memory_recall` closure + `__memory_recall__` tag in `_emit` closure. Callback threaded through `_create_agent` and `_run_agent`. Writes `event: hermes.memory.recalled\ndata: {...}` SSE frame before content frames.
- `tests/gateway/test_memory_recall_sse.py` (new, 2 behavioural probes, both PASS).

**Openwebui side**:
- `backend/open_webui/pipes/hermes_agent.py` marker `HERMES-HOOK-MEMORY-RECALL-PIPE` (240-251) — parses `event: hermes.memory.recalled` frames parallel to existing `hermes.tool.progress` handler; dispatches `__event_emitter__` status event with `action="hermes_memory_recall"` payload `{provider, context_preview, context_token_estimate}`.
- `backend/open_webui/test/pipes/test_hermes_memory_recall.py` (new, 2 behavioural probes, both PASS).

**Patchmap**: 6 markers registered (4 W1 + 2 W2). `verify_hermes_hooks.sh` exits 0 with "OK: all 6 registered markers present and structurally balanced."

## Architectural decision

Callback-based plumbing (new `memory_recall_callback`) rather than polluting existing tool-progress path. Parallels the established `tool_progress_callback` pattern, so future waves can add more first-class callbacks (continuation, skill-invoked, etc.) without entangling them.

## What Was Given Up (carried to W2b continuation)

- `src/lib/components/hermes/MemoryChip.svelte` — the actual visible chip
- Message-render slot injection on `src/lib/components/chat/Messages/Message.svelte` (or equivalent)
- Playwright e2e against live openwebui+hermes stack validating chip appearance

Reason: user explicit boost mode with ~25 min remaining at W2 start. Backend plumbing is the load-bearing contract; UI rendering is additive on top of a stable contract. Deferring UI to a continuation does not leak any Tier-B-style falsely-claimed behaviour — the chip simply does not exist yet, and the `__event_emitter__` events are silently unhandled by the current frontend until W2b lands the component.

## Deferred Queue For Replanning

Promoted to a new wave `W2b — Memory Chip UI` (to be inserted into `decomposition.md` at next session start). W2b allowed-files-to-modify:
- `src/lib/components/hermes/MemoryChip.svelte` (new)
- `src/lib/components/chat/Messages/Message.svelte` (or whichever message-render component is the right slot — requires discovery) with `<!-- HERMES-HOOK-MEMORY-RECALL-UI-BEGIN/-END -->` marker
- `e2e/tests/hermes-memory-chip.spec.ts` (new Playwright behavioural test)

W2b success criteria: chip actually renders on assistant message when `hermes_memory_recall` event fires; absent when it doesn't. Cross-tenant smoke: two users in same chat → chip only shows for the user whose memory was recalled. (Note: Tier B still fundamentally deferred because holographic doesn't scope per-user — so the cross-tenant smoke needs a provider upgrade first. Document this W2b pre-requirement clearly.)

## Behavioural Verifications Run

- `tests/gateway/test_memory_recall_sse.py` — 2 probes PASS (non-empty emits, empty does not).
- `test/pipes/test_hermes_memory_recall.py` — 2 probes PASS (recall event triggers emitter, absent leaves silent).
- `bash scripts/verify_hermes_hooks.sh` — exit 0, 6 markers registered and balanced.

## Unresolved Findings

None at T1 close. UI deferrals are explicit scope descopes, not unresolved blockers.

## Files Modified (absolute paths)

openwebui:
- `backend/open_webui/pipes/hermes_agent.py` (third marker pair added)
- `backend/open_webui/test/pipes/test_hermes_memory_recall.py` (new)
- `docs/hermes-rebase-patchmap.md` (W2 entries populated)

hermes-agent:
- `gateway/platforms/api_server.py` (second marker pair added)
- `run_agent.py` (second marker pair added)
- `tests/gateway/test_memory_recall_sse.py` (new)

## Wave Summary

W2 T1 delivered the backend plumbing for memory recall signalling: honcho/holographic/any provider's `prefetch()` return value now flows through a dedicated `memory_recall_callback` into the SSE stream as a distinct `hermes.memory.recalled` event, which openwebui pipe translates into a frontend-addressable `__event_emitter__` status. Six markers now registered across both repos. Callback architecture is compositional — future waves can add parallel first-class callbacks (continuation, skill-invocation) without entangling them with existing paths. UI chip rendering explicitly deferred to a W2b continuation wave.

Next session pickup: either W2b (finish memory chip UI with Playwright behavioural test against a live stack) or jump to W3 profile panel. Memo I-3 and I-4 items unchanged.

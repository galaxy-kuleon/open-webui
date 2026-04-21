# Wave 1 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Wire openwebui `__user__` identity into hermes-agent's `memory_manager.initialize_all` kwargs via `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` headers, verified at kwargs layer (Tier A).
**Turns executed**: 3 (T1, T2, T3) — all PASS with one orchestrator-level uv.lock revert during T2 verification
**Date**: 2026-04-21
**Commits**:
- openwebui `d96532428` on `feat/v0.8.12-hermes-port`
- hermes-agent `78dd81c0` on `feat/kuleon-openwebui-identity` (new branch, origin: galaxy-kuleon/hermes-agent fork)

## Turn Log

### T1 (openwebui side) — PASS

**Kind 1 work**:
- `backend/open_webui/hermes/__init__.py` (new)
- `backend/open_webui/hermes/identity.py` (new, 70 lines) — `resolve_hermes_identity(user)` with 3-tier tenancy resolution (group.meta.tenancy → group.id → user.id). Defensive: `Optional[dict]` meta guarded, `isinstance(tenancy, str)` type-safe, no sentinel literals.
- `backend/open_webui/pipes/hermes_agent.py` — 2 marker pairs (`HERMES-HOOK-IDENTITY-PIPE`) at lines 124-126 + 133-137. Pipe now consumes `__user__` (previously ignored), injects both headers when identity resolves. Absent when `__user__=None` (fallback symmetry).
- `backend/open_webui/test/hermes/test_identity.py` (new, 8 tests)
- `backend/open_webui/test/pipes/test_hermes_agent_headers.py` (new, 3 async tests)

**Kind 2 verdict**: PASS with 5 non-blocker observations. 3 probes A-E + 5 deep-audit findings. Notable: whitespace stripping omission on identity return + 3-group first-wins test gap — both folded into T2.

### T2 (hermes-agent side + T1 revisit) — PASS

Two Mode B directive clarifications required first:
1. Header name drift (kind 3 initially wrote `X-Hermes-Acting-User*` — corrected to `X-Hermes-User-Id` / `X-Hermes-Tenant-Id`).
2. Kwarg name drift (`acting_user_id` → `user_id`, matching memo I-1 + honcho `kwargs.get("user_id")` contract at `plugins/memory/honcho/__init__.py:293`).

**Kind 1 work**:
- `hermes-agent/gateway/platforms/api_server.py` — 2 marker pairs:
  - `HERMES-HOOK-IDENTITY-API-SERVER-HEADER` (817-825): extract headers, `.strip()`, empty-after-strip → absent
  - `HERMES-HOOK-IDENTITY-API-SERVER-AGENT` (614-623): `_create_agent` accepts kwargs, forwards via `**identity_kwargs` omitting falsy
- `hermes-agent/run_agent.py` — 1 marker pair `HERMES-HOOK-IDENTITY-AIAGENT` (1417-1422). `tenant_id` kwarg added to `AIAgent.__init__` (user_id already existed pre-wave for gateway sessions — kind 1 correctly reused it). Populates `_init_kwargs` so `memory_manager.initialize_all(**_init_kwargs)` forwards to providers.
- `hermes-agent/tests/gateway/test_api_server_identity_header.py` (new, 3 tests — extended to 4 in T3)

**Revisit (T1, contract-alignment lens)**:
- Edit A: `identity.py:63` `return tenancy` → `return tenancy.strip()` (source-side whitespace normalisation).
- Edit B: new test `test_first_wins_across_non_contiguous_tenancy_groups` in `test_identity.py` pinning the first-wins invariant across 3 groups.

**Kind 2 verdict**: FAIL on single surgical blocker — stray `uv.lock` 214-line upstream drift that kind 1 didn't fully revert. Source + tests 100% correct. Orchestrator (kind 7) ran `git checkout HEAD -- uv.lock`, re-verified tests still green, flipped to PASS per kind 2's explicit condition ("run `git checkout HEAD -- uv.lock`, re-run pytest, T2 flips clean to PASS").

### T3 (cross-validation + close) — PASS (behavioural; pytest baseline de-prioritised per user directive mid-wave)

**Kind 1 work**:
- Populated `docs/hermes-rebase-patchmap.md` with 4 registered markers (1 openwebui + 3 hermes-agent) with actual file:line ranges.
- `bash scripts/verify_hermes_hooks.sh` exits 0 with "OK: all 4 registered markers present and structurally balanced."
- **Tier A kwargs-assertion probe** added as test 4 in `test_api_server_identity_header.py`. Probe spies on `MemoryManager.initialize_all`; confirms `user_id="alice"` + `tenant_id="acme"` arrive as kwargs end-to-end from HTTP request.
- End-to-end verbal trace with real file:line anchors covering pipe → httpx → api_server → `_run_agent` → `_create_agent` → `AIAgent.__init__` → `memory_manager.initialize_all` → honcho `kwargs.get("user_id")`.

**Revisit (T1+T2, global-consistency lens)**:
- Findings: (1) PIPE marker uses `[...]` bracket style, hermes markers use `# ...` style — verify_hermes_hooks regex matches both, non-issue. (2) T2 test spy was at `_create_agent` boundary — T3 Tier A probe closed the gap to `memory_manager.initialize_all`. (3) No contract slip between T1 and T2.
- Adjustments: none to T1/T2 source. Patchmap populated + Tier A probe added.

## What Was Tried But Did Not Work

- T2 first attempt had stray `uv.lock` drift from kind 1's transient `uv add pytest-asyncio pytest-xdist` (to install missing test deps). `git restore pyproject.toml` reverted pyproject but not lockfile. Kind 2 caught. Orchestrator reverted with `git checkout HEAD -- uv.lock`. Lesson: if you run `uv add` during a wave, restore BOTH `pyproject.toml` AND `uv.lock`.

## What Was Considered But Not Tried (Deferred)

**From T1 kind-2 observations (non-blocker, deferred to post-wave or future consolidation)**:
1. `resolve_hermes_identity` DB error path propagates above pipe's `try:` block — pre-existing pipe pattern, not a new weakness.
2. Non-string tenancy silently skipped without logging — observability-only.
3. `GroupModel.meta` None-guard — kind 1 already added, noted for documentation.

**From memo amendment**:
- Tier B live per-user isolation — requires provider that scopes per-user. Holographic does NOT. Revisit when W2 UX drives provider-upgrade decision (P1: honcho self-host / P2: patch holographic).

**User directive mid-wave**:
- pytest suite runs de-prioritised in favour of behavioural verification. Going forward, W2+ acceptance relies on behavioural probes (in-process spies, Playwright for UI) rather than unit-test-count preservation.

## What Was Given Up

Nothing. W1 closed all of I-1 fully. Tier A acceptance achieved end-to-end.

## Deferred Queue For Replanning

None promoted to future waves. Items above remain as triage candidates, not blockers.

## Unresolved Findings

None at wave close.

## Files Modified (absolute paths)

openwebui (`/Users/noelbao/Works/open-webui`, commit `d96532428`):
- `backend/open_webui/hermes/__init__.py` (new)
- `backend/open_webui/hermes/identity.py` (new, 70 lines)
- `backend/open_webui/pipes/hermes_agent.py` (+ 11 lines, 2 marker pairs)
- `backend/open_webui/test/hermes/__init__.py` (new)
- `backend/open_webui/test/hermes/test_identity.py` (new, 9 tests)
- `backend/open_webui/test/pipes/__init__.py` (new)
- `backend/open_webui/test/pipes/test_hermes_agent_headers.py` (new, 3 tests)
- `docs/hermes-rebase-patchmap.md` (populated)

hermes-agent (`/Users/noelbao/Works/hermes-agent`, commit `78dd81c0` on `feat/kuleon-openwebui-identity`):
- `gateway/platforms/api_server.py` (2 marker regions)
- `run_agent.py` (1 marker region)
- `tests/gateway/test_api_server_identity_header.py` (new, 4 tests including Tier A probe)

## Behavioural Verifications Run

- `bash scripts/verify_hermes_hooks.sh` → exit 0, "OK: all 4 registered markers present and structurally balanced."
- Tier A spy probe traced `user_id="alice"` + `tenant_id="acme"` from HTTP request headers through `memory_manager.initialize_all` kwargs (in-process, no live hermes server required).
- `uv run python -c "import open_webui"` → exit 0.
- Send-side header literal (pipe line 135-136) and recv-side header literal (api_server line 821-822) byte-identical verified.
- Pipe behaviour with `__user__={"id": "alice"}` → both headers present; with `__user__=None` → neither header present (fallback symmetry).
- 3-group non-contiguous first-wins invariant pinned by test.
- Whitespace tenancy stripped on both emit and receive sides (defence in depth).

## Wave Summary

W1 closed COMPLETE in 3 turns with one orchestrator-level uv.lock revert during T2 verification. Key contract delivered: openwebui pipe now consumes `__user__` (previously ignored at line 88), resolves identity via pure function with 3-tier tenancy order (group.meta.tenancy / group.id / user.id, with whitespace normalisation + type safety + Optional meta guard), sets `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` headers byte-exactly matching hermes-agent's extraction. Hermes `_handle_chat_completions` strips headers with empty-as-absent semantics, threads `user_id` + `tenant_id` kwargs through `_run_agent` → `_create_agent` → `AIAgent.__init__` → `memory_manager.initialize_all(**_init_kwargs)` such that honcho's existing `kwargs.get("user_id")` receives the values. Shared pattern: "falsy-elides, truthy-forwards" — empty strings and missing values never leak into memory provider kwargs. Tier A verified end-to-end via in-process spy probe. Tier B (live per-user peer isolation) explicitly deferred because active provider `holographic` does not scope per-user. 4 markers registered in rebase-patchmap with verify_hermes_hooks.sh exits-0 invariant established for future rebases against both upstream forks.

Next: W2 memory recall chip — hermes emits `hermes.memory.recalled` SSE event on honcho prefetch hit, pipe translates to `__event_emitter__`, Svelte MemoryChip renders on message. **Note per user directive mid-W1**: behavioural verification going forward means Playwright against live stack for UI, not pytest.

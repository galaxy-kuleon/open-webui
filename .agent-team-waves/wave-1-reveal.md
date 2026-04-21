# Wave 1 - Reveal Packet

**Wave Objective**: Propagate authenticated openwebui User identity into hermes-agent's memory-provider chain via `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` headers, verified at the `memory_manager.initialize_all` kwargs layer (Tier A per memo Amendment 2026-04-21). No UI change.
**Data-flow segment**: entry (request-ingress + header wiring)
**Blast radius**: smallest-bounded (2 new files openwebui, 3 marker-bracketed edits hermes, 1 patchmap row set)
**Total waves**: 1 of 4

## Spec Slice (from qa-planner memo)

### I-1 (W1) — Identity propagation: openwebui User → hermes memory_manager → memory provider

**Severity**: enabling (blocks every later wave)
**Blast radius**: medium (two repos touched; zero UI change)

**Files in scope**:

openwebui side:

- `backend/open_webui/hermes/__init__.py` — NEW, package init (empty or docstring only)
- `backend/open_webui/hermes/identity.py` — NEW
  - Function: `resolve_hermes_identity(user) -> dict`
  - Input: openwebui `UserModel` or None
  - Output: `{"user_id": str, "tenant_id": str, "display_name": str, "locale": str}` or `None` if user is None
  - Side effects: one DB query to `Groups.get_groups_by_member_id(user.id)`; no writes
- `backend/open_webui/pipes/hermes_agent.py` — MODIFY (our owned code; edit marker-bracketed for clarity)
  - At line 88 the signature already receives `__user__: dict | None` but the body never uses it — THIS is the gap we fix
  - Call `resolve_hermes_identity` on `__user__` near the top of `pipe()`
  - Add `X-Hermes-User-Id` and `X-Hermes-Tenant-Id` to `headers` dict built around line 123, gated on identity-resolution success
  - Keep existing `X-Hermes-Session-Id` gating at line 280 unchanged

hermes-agent side (repo: `/Users/noelbao/Works/hermes-agent`, origin: galaxy-kuleon/hermes-agent, upstream: NousResearch/hermes-agent):

- `gateway/platforms/api_server.py` — MODIFY, marker-bracketed
  - In `_handle_chat_completions` (line 615): read `X-Hermes-User-Id` and `X-Hermes-Tenant-Id` headers alongside the existing `X-Hermes-Session-Id` extraction (line 672)
  - In `_create_agent` (line 512): accept `user_id: str | None = None` and `tenant_id: str | None = None` params; forward to AIAgent constructor call at line 546
- `run_agent.py::AIAgent.__init__` — MODIFY, marker-bracketed
  - Accept `user_id: str | None = None` and `tenant_id: str | None = None` kwargs
  - Forward into the `memory_manager.initialize_all(session_id=..., **kwargs)` call so the kwargs reach providers; honcho already reads `kwargs.get("user_id")` at `plugins/memory/honcho/__init__.py:293` — the same contract applies to all providers via the MemoryProvider ABC
- `plugins/memory/*/__init__.py` — READ-ONLY. Provider implementations are vendor surface (honcho is AGPL, others may vary). Do NOT modify.

shared:

- `docs/hermes-rebase-patchmap.md` — MODIFY (skeleton already committed as `3917d4cad`; W1 T3 populates actual file:line ranges for the 4 markers)
- `scripts/verify_hermes_hooks.sh` — READ-ONLY (committed, already smoke-tested)

new test files (openwebui):

- `backend/open_webui/test/hermes/__init__.py` — NEW
- `backend/open_webui/test/hermes/test_identity.py` — NEW, unit
  - Table-driven: (a) user in group with `meta.tenancy="acme"` → `tenant_id="acme"`, (b) user in group without tenancy meta → `tenant_id=<group.id>`, (c) user with no groups → `tenant_id=<user.id>`, (d) user is None → returns None
- `backend/open_webui/test/pipes/test_hermes_agent_headers.py` — NEW, unit
  - Mock `httpx.AsyncClient.stream` and assert `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` present when `__user__` provided, absent when None

new test files (hermes-agent):

- `tests/api_server/test_identity_header.py` (or equivalent existing test-location convention) — NEW, unit/integration
  - POST to `/v1/chat/completions` with mocked AIAgent; assert agent received `user_id` kwarg matching the header value
  - Assert absence of header falls back to existing default (no exception, no identity kwargs forwarded)

**Behavioural requirements** (all MUST hold):

1. **Pipe identity extraction**. `pipe.pipe(body, __user__={"id": "u1", "name": "Alice", ...}, ...)` calls `resolve_hermes_identity(__user__)` and obtains `{"user_id": "u1", "tenant_id": <resolved>, ...}`.
2. **Tenancy resolution order** (pure function, DB read allowed):
   - First: scan groups via `Groups.get_groups_by_member_id(user_id)`; first group whose `meta.get("tenancy")` is a non-empty string wins; `tenant_id = group.meta["tenancy"]`.
   - Second: if no group has `meta.tenancy`, use the first group's `group.id` as `tenant_id`.
   - Third: if user belongs to no groups, `tenant_id = user.id`.
3. **Headers**. Both `X-Hermes-User-Id: <user_id>` and `X-Hermes-Tenant-Id: <tenant_id>` are set on every HTTP request from pipe to hermes. Absent only when `__user__` is None.
4. **Hermes extraction**. `_handle_chat_completions` reads both headers; non-empty values flow as `user_id` and `tenant_id` kwargs through `_create_agent` → `AIAgent` → `memory_manager.initialize_all`.
5. **Fallback symmetry**. Request without `X-Hermes-User-Id` → hermes behaves exactly as pre-W1 (no regression for CLI / gateway / legacy callers).

**Acceptance criteria (Tier A per Amendment)**:

1. `uv run pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/test_hermes_agent_headers.py` passes.
2. `uv run pytest` on hermes-agent (or equivalent) passes with the new `test_identity_header.py`.
3. **Tier A kwargs assertion** (kind 2 MUST run this live, not just accept mock): write a throwaway integration test that hits the running hermes server at `/v1/chat/completions` with `X-Hermes-User-Id: alice` in headers, intercepts `memory_manager.initialize_all` via a thin monkey-patched subclass / spy, and asserts the spy received `user_id="alice"`. Tear down after. If tearing down is too intrusive, run the assertion at the `AIAgent.__init__` level instead (inject a probe in tests/conftest).
4. Marker comments registered in `docs/hermes-rebase-patchmap.md` with actual file:line ranges:
   - `HERMES-HOOK-IDENTITY-PIPE` in `backend/open_webui/pipes/hermes_agent.py`
   - `HERMES-HOOK-IDENTITY-API-SERVER-HEADER` in `hermes-agent/gateway/platforms/api_server.py` (header extraction region)
   - `HERMES-HOOK-IDENTITY-API-SERVER-AGENT` in `hermes-agent/gateway/platforms/api_server.py` (`_create_agent` kwargs region)
   - `HERMES-HOOK-IDENTITY-AIAGENT` in `hermes-agent/run_agent.py` (AIAgent `__init__` kwargs region)
5. `bash scripts/verify_hermes_hooks.sh` exits 0.
6. All non-negotiable invariants hold.

**Tier B (live per-user isolation) is explicitly DEFERRED** per memo Amendment 2026-04-21. `holographic` provider does NOT scope per-user, so Tier B cannot be validated in W1. Do NOT accept a PASS that claims live isolation using holographic.

**Non-deferrable**: yes.

---

### Non-negotiable invariants (from memo)

These MUST hold at wave close. Breaking any is a blocker, non-deferrable, regardless of severity triaging:

1. **pytest baseline preserved** on openwebui (412 passed / 9 failed / 5 collection errors current) AND hermes-agent (no new failures attributable to our edits).
2. `uv run python -c "import open_webui"` succeeds.
3. **Cross-tenant memory isolation in principle** — holographic cannot demonstrate it at runtime (Tier B deferred), but the code MUST NOT actively undermine isolation (e.g. never fall back to `tenant_id="default"` when `tenant_id` is None in the pipe; instead omit the header and let hermes fall back cleanly).
4. **Honcho server source is vendor-opaque** — not touched this wave (and in fact not installed — holographic is the active provider).
5. **Hermes-agent upstream rebasable** — all edits marker-bracketed.
6. **openwebui upstream compat** — new code confined to `backend/open_webui/hermes/`; pipe modifications only (pipe is our owned file).
7. **Every behavioural claim tested**.
8. **No destructive actions**.
9. **No opportunistic scope creep**.
10. **Private fork** — no upstream PRs.

### Prerequisites re-check (kind 3 T0 must verify these are STILL green before dispatching T1)

1. `hermes memory status` → `Provider: holographic, Status: available ✓`
2. `git -C /Users/noelbao/Works/hermes-agent remote -v` → origin = `git@github.com:galaxy-kuleon/hermes-agent.git`, upstream = `https://github.com/NousResearch/hermes-agent.git`
3. `lsof -iTCP:8000 -sTCP:LISTEN` returns nothing (or solar-pipeline stays killed)
4. `git -C /Users/noelbao/Works/open-webui status --short` has no unexpected uncommitted work (known residuals: `.agent-team-waves/archive-*/*.md` cosmetic mods, previously-staged `e2e/` and `src/` leftovers, untracked `.webui_secret_key` — all carried over from previous runs and not our concern this wave)

## Deferred Items Assigned To This Wave

None.

## Constraints for This Wave

**Allowed files to modify** (exhaustive — if not listed, don't touch):

openwebui:
- `backend/open_webui/hermes/__init__.py` (new)
- `backend/open_webui/hermes/identity.py` (new)
- `backend/open_webui/pipes/hermes_agent.py` (modify, marker-bracketed)
- `backend/open_webui/test/hermes/__init__.py` (new)
- `backend/open_webui/test/hermes/test_identity.py` (new)
- `backend/open_webui/test/pipes/` tree — new test files only
- `docs/hermes-rebase-patchmap.md` (modify tables, T3 only)
- `.agent-team-waves/wave-1.md` (kind 6 writes at close)

hermes-agent:
- `gateway/platforms/api_server.py` (modify, marker-bracketed, 2 regions)
- `run_agent.py` (modify, marker-bracketed, 1 region)
- `tests/` — new test file(s) only, non-interfering with existing test layout

**Forbidden files** (any touch = blocker):

- Everything under `.agent-team-waves/archive-*/`
- `backend/open_webui/utils/sanitize.py`, `utils/tools.py`, `utils/middleware.py`, `utils/skip_rag.py`, `utils/image_analysis.py`, `utils/knowledge_export.py` (all from prior wave runs — READ-ONLY even if needed for context)
- `backend/open_webui/routers/skills.py`, `routers/retrieval.py` (prior-wave surface)
- `backend/open_webui/retrieval/loaders/kg1.py` (prior-wave)
- All `src/` frontend code (W1 has zero UI change)
- hermes-agent: `plugins/memory/*/` (vendor-opaque memory providers, including honcho's AGPL surface)
- hermes-agent: `agent/memory_manager.py` and `agent/memory_provider.py` (the ABC — stable upstream contract)
- Honcho server repo (not installed; not ours)

**Out-of-scope items** (deferred to future waves — do NOT fix opportunistically):

- Honcho self-host setup (deferred per Amendment to future provider-upgrade wave if needed)
- Per-user SQLite scoping for holographic (Amendment option P2)
- SSE event types for memory recall (`hermes.memory.recalled` — W2 scope)
- Profile panel UI (W3 scope)
- Continuation detection (W4 scope)
- Any `ERROR_MESSAGES.DEFAULT(str(e))` leakage cleanup from prior-wave observations
- Any ruff/lint violations outside wave-touched files

## Handoff from Wave N-1

This is the first wave of this /atw run. No prior wave context.

Prior context relevant to this run (purely for kind 1's situational awareness — NOT scope):

- Skill-intercept short-circuit was committed as `b626baba4` before this run began. It does NOT affect W1's work but is present in `utils/middleware.py` if kind 1 reads that file for context.
- The memo + patchmap skeleton + verify_hermes_hooks.sh were committed in `3917d4cad` (prep artefacts) and `6ed9d0cda` (memo amendment). These exist as reference material; do NOT re-touch them in W1 unless the Amendment Tier A/B split needs further clarification (then update memo in a separate commit).

## Success Criteria

1. All files in `Allowed files to modify` produced or modified correctly with marker-bracketed edits where specified.
2. `uv run pytest backend/open_webui/test/` exit 0 with 412 + N new tests passing (412 baseline maintained; N = count of new hermes/ + pipes/ tests, expected 4+ minimum).
3. hermes-agent tests: new `test_identity_header.py` passes; no pre-existing test regresses.
4. Tier A kwargs-assertion integration check (see acceptance criterion 3 above) passes — kind 2 writes and runs the probe, not accepts a claim.
5. `bash scripts/verify_hermes_hooks.sh` exits 0 with ≥ 4 markers registered in `docs/hermes-rebase-patchmap.md`.
6. `bun run check` baseline preserved (9190 errors, unchanged — no frontend touched).
7. `uv run ruff format --check` on all wave-touched openwebui files: exit 0.
8. All non-negotiable invariants hold.
9. Working tree clean except for W1's tracked changes + the pre-existing residuals (archive dir mods, pre-W1 staged e2e/src leftovers, .webui_secret_key).

## Turn Plan (kind 3 T0 may adjust)

Minimum 3 turns per SKILL.md Rule 20. Proposed:

- **T1 (openwebui side)** — Primary: `identity.py` + pipe header injection + 2 new openwebui test files. No revisit (first turn).
- **T2 (hermes-agent side)** — Primary: `api_server.py` (2 marker regions) + `run_agent.py` (1 marker region) + 1 new hermes test file. Revisit scope: T1. Revisit lens: `contract-alignment` (do pipe-sent headers and kwarg names match the reader side? Is the fallback-symmetry invariant preserved on both sides?).
- **T3 (cross-validation + close)** — Primary: populate `docs/hermes-rebase-patchmap.md` with actual file:line ranges, run `verify_hermes_hooks.sh`, run full openwebui + hermes test suites for baseline confirmation, write + run the Tier A kwargs-assertion probe (acceptance criterion 3). Revisit scope: T1 + T2. Revisit lens: `global-consistency` (mandatory cross-validation turn per SKILL.md §3 Rule 10a / Rule 5a).

If T3 uncovers blockers that require repair, kind 3 may authorise T4 CONVERGENCE MODE. Hard cap T5.

## Notes for principal-engineer (kind 1)

- **Working tree quirks**: `git status` shows pre-existing residuals from prior sessions. Ignore them for your wave's commit; only stage files you actually modified for W1.
- **Cross-repo work**: T2 operates in `/Users/noelbao/Works/hermes-agent` (different repo). Use `git -C` or `cd` carefully. Verify hermes-agent test runner before editing — run one existing test first to confirm environment.
- **Marker syntax**: Python → `# HERMES-HOOK-<NAME>-BEGIN` + `# HERMES-HOOK-<NAME>-END`. Keep marker blocks small (single logical edit per bracket). Do NOT nest.
- **`Groups.get_groups_by_member_id`** is defined in `backend/open_webui/models/groups.py:292`. Already imports available via `from open_webui.models.groups import Groups`.
- **Pipe `__user__` shape**: per Open WebUI pipe convention, it's a dict like `{"id": "...", "name": "...", "email": "...", "role": "..."}`. Handle it as dict, not UserModel (the pipe receives the dict form).

## Notes for evaluator (kind 2)

- **Tier A is load-bearing**. Mock-based unit tests are NOT sufficient for W1 close. You MUST actually run the integration probe that hits a live hermes server (or at least runs the relevant Python paths with real imports) and verifies `memory_manager.initialize_all` receives `user_id` kwarg. Cite the exact command you ran.
- **Tier B is OFF the table this wave**. If kind 1 claims "I verified isolation with holographic", that's a FAIL (holographic doesn't scope per-user; any claim otherwise is fabricated).
- **Cross-repo test verification**: run hermes-agent's test suite yourself with `uv run pytest` (or equivalent) in that repo. Don't trust kind 1's summary.
- **Marker hygiene**: after kind 1 registers the 4 markers in patchmap, YOU re-run `bash scripts/verify_hermes_hooks.sh` from a clean terminal and paste its output as verdict evidence.
- **Header absence is a required behaviour**: verify that requests without `X-Hermes-User-Id` still succeed (fallback symmetry). If kind 1 broke the pre-W1 code path, that's a FAIL.

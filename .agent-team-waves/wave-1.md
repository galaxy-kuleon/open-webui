# Wave 1 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Land collision-free NEW Hermes modules on v0.9.1 via `git checkout hermes-v0.8.12-final -- <path>`, bump `package.json` to `0.9.1+hermes.0`, verify imports + targeted pytest + frontend typecheck pass with zero upstream file modification beyond minimum graft.
**Turns executed**: 3 (of budget 3) — T1 ran as initial + 2 REDIRECTs before close; T2 primary + REDIRECT graft; T3 mandatory cross-validation
**Master directives issued**: 4
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-23

## Turn Log

### T1

- **Master directive for this turn**: T0 plan — enumerate NEW-file set via `git diff v0.9.1..hermes-v0.8.12-final --diff-filter=A --name-only`, intersect with spec, apply forbidden filter, materialize via `git checkout`, bump package.json, import smoke, stage + self-check.
- **Principal work**:
  - Enumerated 78 admissible NEW files from `git diff v0.9.1..hermes-v0.8.12-final --diff-filter=A --name-only`
  - Breakdown: 14 backend-core modules, 40 backend tests, 4 frontend files, 20 e2e files
  - All 78 files checked out clean; zero forbidden file staged
  - `package.json` bumped `0.9.1` → `0.9.1-hermes.0` initially, then corrected to `0.9.1+hermes.0` (PEP 440 compliance: `-` rejected by `packaging.version.Version`; `+` accepted as local version identifier)
  - Import smoke partial failure: `hermes.identity` and `pipes.hermes_agent` resolved; `routers.hermes_memory` failed (missing `HERMES_API_URL`); `utils.skip_rag` failed (missing `sanitize_filename`, `sanitize_llm_injected_markdown`, `_SKIP_RAG_MAX_BYTES`)
- **Master REDIRECT-AND-DESCOPE issued**: authorize minimum additive graft of hermes-only symbols into `env.py` and `sanitize.py` (kept to symbols Wave 1 NEW files import only; full delta deferred)
- **T1 REDIRECT graft work**:
  - `backend/open_webui/env.py`: hand-grafted +14 lines (HERMES_API_URL, OPENCODE_PATH, etc.) — hand-graft only (not full `git checkout`) because upstream `env.py` has hermes-branch modifications (SQLite PRAGMA removed, torch detection changed) that would collide
  - `backend/open_webui/utils/sanitize.py`: `git checkout hermes-v0.8.12-final -- backend/open_webui/utils/sanitize.py` (strict additive superset, +177 lines prepended before `import re`)
  - `pyproject.toml`: `anyascii==0.3.3` added (real dependency of sanitize.py)
  - `uv.lock`: auto-updated by `uv sync`
  - All 4 sentinel imports now resolve
- **Evaluator T1 graft verdict**: FAIL — caught `Groups.get_groups_by_member_id` sync-called at `backend/open_webui/hermes/identity.py` (async in v0.9.1). Adversarial `AsyncMock` repro confirmed `TypeError: 'coroutine' object is not iterable`. 8 of 9 tests in `test_identity.py` failed. Production runtime 500 on every tenant-scoped call.
- **Master REDIRECT issued**: fix async propagation across 5 call sites + 34 NEW-file Svelte TS errors + produce upstream-import audit inventory
- **T1 retry work**:
  - `backend/open_webui/hermes/identity.py:15`: `resolve_hermes_identity` made `async def`
  - `backend/open_webui/pipes/hermes_agent.py:128`: `await` added to `resolve_hermes_identity` call
  - `backend/open_webui/routers/hermes_memory.py:123,141,160,184`: `await` added at 4 call sites
  - `backend/open_webui/test/hermes/test_identity.py`: updated from sync `patch(return_value=...)` to `AsyncMock` + `@pytest.mark.asyncio` + `async def` test functions
  - `src/lib/components/chat/Settings/HermesMemory.svelte`: `getContext<Writable<I18nType>>('i18n')` with proper `svelte/store` + `i18next` type imports — fixed 23 TS errors
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/AgentSkillStatus.svelte`: `/** @type {Record<string, any> | null} */` JSDoc — fixed 7 TS errors
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`: same JSDoc fix — fixed 4 TS errors
  - `package-lock.json` staged; `static/pyodide/pyodide-lock.json` NOT staged (unrelated pathspec drift)
  - Upstream-import audit inventory produced (table covering 7 files)
- **Evaluator T1 retry verdict**: FAIL — verified 5 async fixes applied; TS baseline restored (9381). Adversarial sweep found `Files.get_file_by_id(fid)` at `backend/open_webui/pipes/hermes_agent.py:339` sync-called (async in v0.9.1). Additionally found 3 latent-but-unreachable async mismatches: `utils/builtin_pipes.py` (6 sites), `utils/image_analysis.py` (7 sites), `utils/skill_params.py` (2 sites). FAIL with routing recommendations for the 3 deferred items.
- **Master REDIRECT-AND-DESCOPE issued**: graft `Files.get_file_by_id` async fix into T2 primary + regression test with RED/GREEN proof; descope 3 unreachable async mismatches to target waves' deferred queues.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: FAIL (after 2 retries within T1)
- **Evaluator commands run**:
  - `AsyncMock` adversarial repro confirming `TypeError: 'coroutine' object is not iterable` for `Groups.get_groups_by_member_id`
  - TS baseline count check confirming 9381 errors = upstream baseline
  - Adversarial sweep of `Files.get_file_by_id` call sites across hermes NEW files
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/hermes/identity.py:15`: `resolve_hermes_identity` was sync but `Groups.get_groups_by_member_id` is async in v0.9.1
  - `backend/open_webui/pipes/hermes_agent.py:339`: `Files.get_file_by_id(fid)` sync-called (async in v0.9.1)
  - `backend/open_webui/pipes/hermes_agent.py:332`: `UPLOAD_DIR` imported from `open_webui.env` (symbol absent there in v0.9.1) — secondary bug found during T2 sweep
  - `src/lib/components/chat/Settings/HermesMemory.svelte`: `getContext` cast `as Writable<I18nType>` caused 23 TS errors; correct pattern is typed generic `getContext<Writable<I18nType>>('i18n')`
  - `package.json` version `-hermes.0` rejected by PEP 440 `packaging.version.Version`; `+hermes.0` accepted as local version identifier
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/hermes/__init__.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/hermes/identity.py` (NEW, checked out; then async fix)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/__init__.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py` (NEW, checked out; then async fix at :128)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/hermes_memory.py` (NEW, checked out; then async fixes at :123,:141,:160,:184)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/retrieval/loaders/kg1.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skip_rag.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/opencode.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/knowledge_export.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/image_analysis.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skill_params.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/docling.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/lmstudio_memory.py` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/builtin_pipes.py` (NEW, checked out)
  - All 40 backend test files under `backend/open_webui/test/hermes/`, `test/pipes/`, `test/utils/`, `test/routers/`, `test/fixtures/`, `test/apps/webui/` (NEW, checked out; `test_identity.py` also patched for AsyncMock)
  - `/Users/noelbao/Works/open-webui/src/lib/apis/hermes/memory.ts` (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/src/lib/components/chat/Settings/HermesMemory.svelte` (NEW, checked out; then TS fix)
  - `/Users/noelbao/Works/open-webui/src/lib/components/chat/Messages/ResponseMessage/StatusHistory/AgentSkillStatus.svelte` (NEW, checked out; then JSDoc fix)
  - `/Users/noelbao/Works/open-webui/src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte` (NEW, checked out; then JSDoc fix)
  - 20 e2e spec/helper/fixture files (NEW, checked out)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/env.py` (MOD, +14 lines hand-graft)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/sanitize.py` (MOD, `git checkout` superset +177 lines)
  - `/Users/noelbao/Works/open-webui/package.json` (MOD, version bump)
  - `/Users/noelbao/Works/open-webui/package-lock.json` (MOD, version mirror)
  - `/Users/noelbao/Works/open-webui/pyproject.toml` (MOD, +anyascii==0.3.3)
  - `/Users/noelbao/Works/open-webui/uv.lock` (MOD, auto-regenerated by uv sync)

### T2

- **Master directive for this turn**: REDIRECT-AND-DESCOPE — graft `Files.get_file_by_id` async fix into T2 primary + regression test with RED/GREEN proof; route 3 unreachable async mismatches to target waves.
- **Principal work**:
  - `backend/open_webui/pipes/hermes_agent.py:339`: `record = await Files.get_file_by_id(fid)` (async fix)
  - `backend/open_webui/pipes/hermes_agent.py:332`: `from open_webui.config import UPLOAD_DIR` (secondary bug fix — symbol absent from `open_webui.env` in v0.9.1)
  - NEW regression test file written: `backend/open_webui/test/pipes/test_hermes_file_paths.py` (3 tests)
  - RED proof: without `await`, tests fail with exact `'coroutine' object has no attribute 'path'`
  - GREEN proof: with `await`, 3/3 pass
  - Broadened audit of 7 more files (`hermes/identity.py`, `retrieval/loaders/kg1.py`, `utils/knowledge_export.py`, `utils/opencode.py`, `utils/docling.py`, `utils/lmstudio_memory.py`, `utils/skip_rag.py`): all open_webui.\* call sites clean
  - Deferred queue routing confirmed: `builtin_pipes` async mismatch → Wave 4; `image_analysis` async mismatch → Wave 5; `skill_params` async mismatch → Wave 3
  - `pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/ backend/open_webui/test/utils/test_builtin_pipes.py -q`: 23 passed, 25 warnings
  - `pytest --collect-only` Wave-1 dirs: 343 collected, 8 errors (all enumerated deferrals)
  - `npm run check`: 9381 errors / 377 files-with-problems = baseline (zero Wave-1-attributable delta)
  - Retrospective annotations: corrected deferred count to 8 (not 9); documented 3 async mismatch target waves; documented PEP 440 `+hermes.0` rationale; documented new regression test file; documented `UPLOAD_DIR` secondary bug
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - Independent `sed` revert of `await` on `hermes_agent.py:339` + re-run to confirm RED
  - Production-site sweep: 11 `Files.get_file_by_id` sites + 15 `generate_chat_completion` sites + 1 `get_builtin_tools` site — all confirmed correctly awaited
  - Full async-contract sweep of Wave 1 runtime surface: no further violations found
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/pipes/hermes_agent.py:332`: `UPLOAD_DIR` was imported from `open_webui.env` but the symbol lives in `open_webui.config` in v0.9.1 — would have caused `ImportError` at runtime
  - `backend/open_webui/pipes/hermes_agent.py:339`: `Files.get_file_by_id` sync-called; `'coroutine' object has no attribute 'path'` confirmed behaviorally via RED test
  - Evaluator advisory: Wave 4 reveal packet should explicitly name the 4 cascade tests (`test_t2_cascade_wiring.py`, `test_t3_edge_cases.py`, `test_token_cascade_tier3_failure.py`, `test_w2t2_phase_a_cascade.py`) as DoD items to prevent silent pass-through when `apply_token_budget_cascade` is grafted into middleware.py
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py` (MOD, async fix :339 + import fix :332)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/pipes/test_hermes_file_paths.py` (NEW, regression test added)

### T3

- **Master directive for this turn**: PROCEED AS PLANNED — T3 mandatory close-checklist (7 items, verification-only). Kind 3 rejected evaluator recommendation to skip T3.
- **Principal work**:
  1. File presence: all 78 NEW files + imports reachable; `reachable: ok`
  2. Version: `jq -r .version package.json` = `0.9.1+hermes.0`; single declaration site confirmed
  3. Verification: 23 pytest pass; `npm run check` = 9381 baseline
  4. Runtime-blocker fix present: `await Files.get_file_by_id(fid)` at `backend/open_webui/pipes/hermes_agent.py:339`
  5. Secondary fix present: `from open_webui.config import UPLOAD_DIR` at `backend/open_webui/pipes/hermes_agent.py:332`
  6. Deferred-queue routing PASS for all 9 hermes-port collection errors + 3 async mismatches; distinguished 5 pre-existing upstream collection errors (`test_auths/models/users` `No module named 'test'`; `test_provider` `No module named 'moto'`; `test_redis` `MAX_RETRY_COUNT`) as NOT Wave 1 scope
  7. Wave close summary block produced
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `git diff --diff-filter=A --name-only | wc -l` = 78 (NEW file count confirmed)
  - `git diff v0.9.1 --stat` = empty for pre-existing upstream collection errors (confirming they predate Wave 1)
  - Verification of 9 hermes-specific deferred collection errors routed by wave: Wave 3×2 (skills_upload, skill_params async), Wave 4×6 (token cascade tests, middleware sanitizer, document_index, retrieval_config, validate_admin_dir), Wave 5×1 (image_analysis async)
  - Note: evaluator grouped `test_validate_admin_dir` under Wave 3 but per decomposition it routes to Wave 4; actual routing Wave 4
  - No blockers found; wave close approved
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - 5 pre-existing upstream collection errors confirmed via `git diff v0.9.1 --stat` empty: `test_auths.py`, `test_models.py`, `test_users.py` (`No module named 'test'`), `test_provider.py` (`No module named 'moto'`), `test_redis.py` (`MAX_RETRY_COUNT` renamed to `REDIS_SENTINEL_MAX_RETRY_COUNT` in v0.9.1)
  - All 7 MOD files correctly staged; `static/pyodide/pyodide-lock.json` excluded (unstaged working-tree M, unrelated pathspec drift)
  - Wave 4 advisory carried forward: explicitly name 4 cascade tests as DoD items in Wave 4 reveal packet
- **Cross-turn findings**:
  - Two independent async-contract bugs found across T1 and T2: (1) `Groups.get_groups_by_member_id` (5 call sites, caught T1); (2) `Files.get_file_by_id` (1 call site, caught T1-retry). Both fixed. Combined with secondary `UPLOAD_DIR` import-source bug at `:332`, all three would have been silent runtime failures.
  - PEP 440 version format: `0.9.1-hermes.0` (reveal packet spec) silently rejected by Python `packaging.version.Version`; corrected to `0.9.1+hermes.0` (local version identifier). Reveal packet spec value should be updated for Wave 2+.
  - `sanitize.py` was a strict additive superset and could be checked out wholesale; `env.py` could not (upstream-branch modifications on same file required hand-graft of hermes-only symbols only).

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

- `package.json` version set to `0.9.1-hermes.0` (per reveal packet spec): silently rejected by Python `packaging.version.Version` at import smoke time. Corrected to `0.9.1+hermes.0`.
- `from open_webui.env import UPLOAD_DIR` in `hermes_agent.py:332`: `UPLOAD_DIR` does not exist in `open_webui.env` on v0.9.1; it lives in `open_webui.config`. Corrected during T2.
- `getContext('i18n') as Writable<I18nType>` cast pattern in `HermesMemory.svelte`: generated 23 TS errors in v0.9.1 typecheck environment. Corrected to typed generic `getContext<Writable<I18nType>>('i18n')`.
- `resolve_hermes_identity` as `def` (sync): `Groups.get_groups_by_member_id` is `async` in v0.9.1; sync call produced `'coroutine' object is not iterable` at runtime. Required rewrite to `async def` + `await` at 5 call sites.
- `Files.get_file_by_id(fid)` without `await` in `hermes_agent.py:339`: `Files.get_file_by_id` is `async` in v0.9.1; unawaited call yielded a coroutine object; `.path` attribute access produced `AttributeError` at runtime. Fixed with `await`.

## What Was Considered But Not Tried (Deferred)

**Async mismatches — latent, no Wave 1 runtime caller:**

- `backend/open_webui/utils/builtin_pipes.py:41,43,57,69,85,99`: 6 call sites on `Users.get_super_admin_user`, `Users.get_first_user`, `Functions.get_function_by_id`, `Functions.update_function_by_id`, `Functions.insert_new_function` unawaited. `ensure_builtin_pipes` not yet wired to `main.py` lifespan. Routed to **Wave 4**.
- `backend/open_webui/utils/image_analysis.py:172,239,256,260,264,273,285`: 7 call sites on `Files.update_file_data_by_id` unawaited. Image analysis not yet invoked from any Wave 1 runtime path. Routed to **Wave 5**.
- `backend/open_webui/utils/skill_params.py:144,146`: 2 call sites on `Users.get_super_admin_user`, `Users.get_first_user` unawaited. Skill system not yet wired. Routed to **Wave 3**.

**Pytest collection errors — hermes-port-specific (9 total):**

- `backend/open_webui/test/routers/test_skills_upload.py`: missing `_assert_no_symlinks_in_tree`. Routed to **Wave 3**.
- `backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py`: missing `_SKIP_RAG_PREAMBLE` (from middleware graft not yet landed). Routed to **Wave 4**.
- `backend/open_webui/test/routers/test_document_index_db_session.py`: missing `generate_document_index`. Routed to **Wave 4**.
- `backend/open_webui/test/routers/test_retrieval_config.py`: missing `generate_document_index`. Routed to **Wave 4**.
- `backend/open_webui/test/routers/test_validate_admin_dir.py`: missing `_validate_admin_dir`. Routed to **Wave 4** (decomposition-authoritative; evaluator suggested Wave 3 but decomposition.md assigns this to Wave 4).
- `backend/open_webui/test/utils/test_t2_cascade_wiring.py`: missing `apply_token_budget_cascade` from `middleware.py`. Routed to **Wave 4**.
- `backend/open_webui/test/utils/test_t3_edge_cases.py`: same. Routed to **Wave 4**.
- `backend/open_webui/test/utils/test_token_cascade_tier3_failure.py`: same. Routed to **Wave 4**.
- `backend/open_webui/test/utils/test_w2t2_phase_a_cascade.py`: same. Routed to **Wave 4**.

**Pre-existing upstream collection errors (5 — NOT Wave 1 scope, confirmed via `git diff v0.9.1 --stat` empty):**

- `test_auths.py`, `test_models.py`, `test_users.py`: `No module named 'test'` — pre-existing import pattern from v0.9.1 baseline
- `test_provider.py`: `No module named 'moto'` — missing optional dependency, pre-existing
- `test_redis.py`: `MAX_RETRY_COUNT` — renamed to `REDIS_SENTINEL_MAX_RETRY_COUNT` in v0.9.1 baseline

**Full `env.py` and `sanitize.py` hermes deltas:**

- Remainder of hermes-only env vars in `env.py` not imported by Wave 1 NEW files — deferred to Wave 2 (alongside `config.py` env var additions).
- Remainder of hermes-only helpers in `sanitize.py` not imported by Wave 1 NEW files — deferred to Wave 3 or the wave that first imports them.

**`src/lib/apis/skills/index.ts` MODIFIED delta:**

- Skill API additions from hermes branch — deferred to Wave 3 (skill system port).

## What Was Given Up

None

## Deferred Queue For Replanning

- `backend/open_webui/utils/builtin_pipes.py:41,43,57,69,85,99` — 6 async mismatches on Users/Functions ORM methods; no runtime caller yet. **Destination: Wave 4**. Wave 4 reveal packet must list these as pre-existing blockers that activate when `ensure_builtin_pipes` is wired to `main.py` lifespan.
- `backend/open_webui/utils/image_analysis.py:172,239,256,260,264,273,285` — 7 async mismatches on `Files.update_file_data_by_id`; no runtime caller yet. **Destination: Wave 5**. Wave 5 reveal packet must list these as pre-existing blockers that activate when image-analysis feature integrates.
- `backend/open_webui/utils/skill_params.py:144,146` — 2 async mismatches on `Users.get_super_admin_user`, `Users.get_first_user`; no runtime caller yet. **Destination: Wave 3**. Wave 3 reveal packet must list these as pre-existing blockers that activate when skill system port invokes `skill_params`.
- `backend/open_webui/test/routers/test_skills_upload.py` — collection error: missing `_assert_no_symlinks_in_tree`. **Destination: Wave 3**.
- `backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` — collection error: missing `_SKIP_RAG_PREAMBLE`. **Destination: Wave 4**.
- `backend/open_webui/test/routers/test_document_index_db_session.py` — collection error: missing `generate_document_index`. **Destination: Wave 4**.
- `backend/open_webui/test/routers/test_retrieval_config.py` — collection error: missing `generate_document_index`. **Destination: Wave 4**.
- `backend/open_webui/test/routers/test_validate_admin_dir.py` — collection error: missing `_validate_admin_dir`. **Destination: Wave 4**.
- `backend/open_webui/test/utils/test_t2_cascade_wiring.py` — collection error: missing `apply_token_budget_cascade` from `middleware.py`. **Destination: Wave 4**. Advisory: Wave 4 reveal packet should name all 4 cascade tests (`test_t2_cascade_wiring.py`, `test_t3_edge_cases.py`, `test_token_cascade_tier3_failure.py`, `test_w2t2_phase_a_cascade.py`) as explicit DoD items.
- `backend/open_webui/test/utils/test_t3_edge_cases.py` — collection error: missing `apply_token_budget_cascade`. **Destination: Wave 4**.
- `backend/open_webui/test/utils/test_token_cascade_tier3_failure.py` — collection error: missing `apply_token_budget_cascade`. **Destination: Wave 4**.
- `backend/open_webui/test/utils/test_w2t2_phase_a_cascade.py` — collection error: missing `apply_token_budget_cascade`. **Destination: Wave 4**.
- `backend/open_webui/env.py` hermes delta remainder — env vars not referenced by Wave 1 NEW files. **Destination: Wave 2**.
- `backend/open_webui/utils/sanitize.py` hermes delta remainder — helpers not referenced by Wave 1 NEW files. **Destination: Wave 3 or the wave that first imports them**.
- `src/lib/apis/skills/index.ts` MODIFIED delta — skill API additions. **Destination: Wave 3**.

## Unresolved Findings

None - wave closed clean

## Files Modified (absolute paths)

**NEW (78 files staged with `A` status):**

- `/Users/noelbao/Works/open-webui/backend/open_webui/hermes/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/hermes/identity.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/routers/hermes_memory.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/retrieval/loaders/kg1.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skip_rag.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/opencode.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/knowledge_export.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/image_analysis.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skill_params.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/docling.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/lmstudio_memory.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/builtin_pipes.py`
- 40 backend test files under `backend/open_webui/test/hermes/`, `test/pipes/`, `test/utils/`, `test/routers/`, `test/fixtures/skills/`, `test/apps/webui/test_middleware_skip_rag_sanitizer.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/pipes/test_hermes_file_paths.py` (regression test, added T2)
- `/Users/noelbao/Works/open-webui/src/lib/apis/hermes/memory.ts`
- `/Users/noelbao/Works/open-webui/src/lib/components/chat/Settings/HermesMemory.svelte`
- `/Users/noelbao/Works/open-webui/src/lib/components/chat/Messages/ResponseMessage/StatusHistory/AgentSkillStatus.svelte`
- `/Users/noelbao/Works/open-webui/src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`
- 20 e2e files (spec files, helpers, zip fixtures, `playwright.config.ts`) under `e2e/`

**MOD (6 files staged):**

- `/Users/noelbao/Works/open-webui/backend/open_webui/env.py` (+14 lines hand-graft: HERMES_API_URL, OPENCODE_PATH, and related hermes-only env vars)
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/sanitize.py` (+177 lines superset prepended before `import re`, via `git checkout hermes-v0.8.12-final`)
- `/Users/noelbao/Works/open-webui/package.json` (version field: `0.9.1` → `0.9.1+hermes.0`)
- `/Users/noelbao/Works/open-webui/package-lock.json` (version mirror, staged)
- `/Users/noelbao/Works/open-webui/pyproject.toml` (+anyascii==0.3.3 dependency)
- `/Users/noelbao/Works/open-webui/uv.lock` (auto-regenerated by `uv sync`)

**Unstaged working-tree M (excluded from commit):**

- `/Users/noelbao/Works/open-webui/static/pyodide/pyodide-lock.json` (unrelated pathspec drift, not staged)

## Behavioral Verifications Run

- `python -c "from open_webui.hermes.identity import resolve_hermes_identity; from open_webui.pipes.hermes_agent import Pipe; from open_webui.routers.hermes_memory import router; from open_webui.utils.skip_rag import build_skip_rag_context; print('reachable: ok')"` — outcome: `reachable: ok` (4/4 sentinel imports resolved after env.py + sanitize.py graft)
- `pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/ backend/open_webui/test/utils/test_builtin_pipes.py -q` — outcome: 23 passed, 25 warnings
- `pytest --collect-only backend/open_webui/test/hermes/ backend/open_webui/test/pipes/ backend/open_webui/test/routers/ backend/open_webui/test/utils/ backend/open_webui/test/apps/` — outcome: 343 collected, 8 errors (all enumerated hermes-port-specific deferrals)
- `npm run check` — outcome: 9381 errors / 377 files-with-problems = upstream baseline; zero Wave-1-attributable delta
- RED/GREEN proof on `backend/open_webui/test/pipes/test_hermes_file_paths.py`:
  - RED: `sed` revert of `await` on `hermes_agent.py:339` → 3 tests fail with `'coroutine' object has no attribute 'path'`
  - GREEN: `await` restored → 3/3 pass
- AsyncMock adversarial repro on `Groups.get_groups_by_member_id`: `TypeError: 'coroutine' object is not iterable` confirmed before async fix; 9/9 `test_identity.py` tests pass after fix
- Production-site sweep (T2 evaluator): 11 `Files.get_file_by_id` sites + 15 `generate_chat_completion` sites + 1 `get_builtin_tools` site — all correctly awaited in Wave 1 runtime surface
- `git diff --diff-filter=A --name-only | wc -l` — outcome: 78 (NEW file count confirmed at T3)
- `git diff v0.9.1 --stat` for pre-existing collection error files — outcome: empty (confirms 5 upstream collection errors predate Wave 1)
- `jq -r .version package.json` — outcome: `0.9.1+hermes.0`

## Wave Summary

Wave 1 closed COMPLETE. Delivered: 78 NEW collision-free Hermes modules + minimum additive graft on `env.py`/`sanitize.py`/`pyproject.toml` + version `0.9.1+hermes.0`. Fixed two runtime async-contract mismatches (`Groups.get_groups_by_member_id` x5 sites; `Files.get_file_by_id` x1 site) + secondary `UPLOAD_DIR` import-source bug at `hermes_agent.py:332`. Test gate: 23/23 hermes-scoped pytest pass; `npm run check` baseline unchanged at 9381. Deferred to Wave 3: `skill_params.py` async mismatch (2 sites), `test_skills_upload.py` collection error. Deferred to Wave 4: `builtin_pipes.py` async mismatch (6 sites), 7 middleware/retrieval collection errors — Wave 4 reveal packet should explicitly name the 4 cascade tests (`test_t2_cascade_wiring.py`, `test_t3_edge_cases.py`, `test_token_cascade_tier3_failure.py`, `test_w2t2_phase_a_cascade.py`) as DoD items. Deferred to Wave 5: `image_analysis.py` async mismatch (7 sites).

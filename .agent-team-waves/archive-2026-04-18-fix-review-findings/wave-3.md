# Wave 3 - Retrospective

**Status**: COMPLETE
**Wave Objective**: wire `run_agent_skill` native-FC registration (F-6/C-2) + harden skip_rag docling markdown injection against prompt injection and DoS (F-7/C-3)
**Turns executed**: 3 + 1 retry (T1, T2, T2-retry, T3) (of budget 4)
**Master directives issued**: 6 (Mode A T0 plan + 4 Mode B inter-turn + 1 Mode B T2 retry adjudication; no ESCALATE or HARD-CONVERGE used)
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-18

## Turn Log

### T1

- **Master directive for this turn**: F-6 only — single-line mirror assignment in middleware.py. No revisit.
- **Principal work**:
  - `backend/open_webui/utils/middleware.py:3270-3273` — added 4-line `__agent_skill_ids__` mirror block: `'__agent_skill_ids__': [s.id for s in available_skills if s.id not in user_skill_ids and s.meta.type == 'agent_skill']`. Access-controlled upstream at middleware.py:2999 via `accessible_skill_ids`. `s.meta.type` safe: `SkillMeta` is Pydantic, not dict, verified via live `model_validate`.
  - `backend/open_webui/test/utils/test_tools_agent_skill_registration.py` (new) — 3 unit tests
  - `backend/open_webui/test/utils/test_middleware_agent_skill_ids.py` (new) — 1 integration probe (self-documented comprehension replica; flagged for W4 F-9 systemic cleanup)
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 291 passed / 9 pre-existing failed / 5 pre-existing collection errors (+4 vs prior baseline)
  - `uv run python -c "import open_webui"` — exit 0
  - `uv run ruff format --check` on W3-touched files — exit 0
  - `bun run check` — 9190 errors (baseline preserved)
  - 10+ independent verifications, 3 deep audits by kind 2
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/utils/middleware.py:2999` — `accessible_skill_ids` upstream access-control gate confirms no access bypass possible via F-6 addition
  - `backend/open_webui/test/utils/test_middleware_agent_skill_ids.py` — integration test is a comprehension replica (same W1 anti-pattern); candidate for W4 F-9 systemic cleanup (non-blocker, pinned to W4)
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_tools_agent_skill_registration.py` (new)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_middleware_agent_skill_ids.py` (new)

### T2

- **Master directive for this turn**: F-7 in full — new pure helper in sanitize.py + middleware.py skip_rag edits + preamble + byte cap + truncation event. Revisit T1 under `contract-alignment` lens.
- **Principal work**:
  - `backend/open_webui/utils/sanitize.py` (M) — added `sanitize_llm_injected_markdown(text, file_id)` with single compiled `_LLM_INJECT_STRIP_RE` stripping U+2028, U+2029, U+200B-U+200D, U+202A-U+202E, U+2066-U+2069, ASCII C0 except `\t\n\r`; `_FILE_ID_VALID_RE` whitelist `[a-zA-Z0-9_-]` raises ValueError on invalid file_id; `_SKIP_RAG_MAX_BYTES = 256 * 1024` constant; delimiter format `<<FILE file-{file_id} BEGIN>>\n…\n<<FILE file-{file_id} END>>`. Helper is idempotent (choice a).
  - `backend/open_webui/utils/middleware.py` (M) — added sanitize import (line 32); `_SKIP_RAG_PREAMBLE` verbatim constant (line 157); rewrote skip_rag injection block (~lines 3305-3540) with: sanitizer call, preamble prepend (idempotent via EXACT-STRING match on first system message), `skip_rag_truncated` event via `event_emitter`, double-injection RAISES (not silent dedupe), byte-safe truncation with END>> re-append; renamed inner `_sanitize_fn` shadow to `_skill_sanitize_fn` to remove shadowing.
  - `backend/open_webui/test/utils/test_sanitize_injected_markdown.py` (new) — 83 unit tests
  - `backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` (new) — 8 REAL integration tests exercising `process_chat_payload` directly (not replica)
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS with 2 non-blocker observations — kind 3 Mode B re-adjudicated both as BLOCKERS; REDIRECT issued for surgical retry
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 382 passed / 9 pre-existing failed / 5 pre-existing collection errors (+91 vs T1 baseline)
  - `uv run python -c "import open_webui"` — exit 0
  - `uv run ruff format --check` on 6 W3-touched files — exit 0
  - `bun run check` — 9190 errors (baseline preserved)
  - 15 adversarial probes: prompt-injection survival, delimiter forgery, file-ID forgery x9 cases, byte cap, multi-byte boundary, preamble idempotence x3 cases, double-injection, empty sanitization, 1 MB regex perf, F-6 contract alignment
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` — `test_preamble_not_duplicated_when_already_present` VACUOUS: routes files via `metadata['files']` instead of `form_data['files']`; `skip_rag_files=[]`; skip_rag block never runs; assertion trivially passes. BLOCKER — false coverage in security-critical path.
  - `backend/open_webui/utils/sanitize.py:186` + `backend/open_webui/utils/middleware.py:3350` — dual `_SKIP_RAG_MAX_BYTES` constant (local re-declaration in middleware.py). Spec required single source of truth. BLOCKER — divergence risk and spec violation.
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/sanitize.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_sanitize_injected_markdown.py` (new)
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` (new)

### T2-retry

- **Extension reason**: T2 evaluator PASS verdict concealed 2 spec violations: vacuous idempotence test (false coverage) + parallel constant (dual source of truth). Kind 3 Mode B re-adjudicated both as blockers requiring surgical retry before advancing to T3.
- **Master directive for this turn**: Two surgical fixes only — (1) import `_SKIP_RAG_MAX_BYTES` from sanitize.py in middleware.py import line, remove local re-declaration; (2) fix vacuous preamble test to route files via `form_data['files']` so skip_rag block actually executes.
- **Principal work**:
  - `backend/open_webui/utils/middleware.py:32` — added `_SKIP_RAG_MAX_BYTES` to the sanitize import; removed local re-declaration at old line 3350.
  - `backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` — changed `test_preamble_not_duplicated_when_already_present` to route files via `form_data['files']` so skip_rag block executes and idempotence guard fires.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries at retry level)
- **Evaluator commands run**:
  - Mental mutation probe: modified middleware.py:3513 `if not _preamble_already_present` → `if True`; re-ran test: FAILED as expected. Reverted: test PASSES, `git diff` clean.
  - 5 adversarial spot-checks: all PASS.
  - `uv run pytest backend/open_webui/test/` — confirmed 382 passed / 9 pre-existing failed / 5 pre-existing collection errors (unchanged from T2 count; fixes were correctness, not count)
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/utils/sanitize.py:186` — confirmed single source of truth for `_SKIP_RAG_MAX_BYTES` after middleware.py local re-declaration removed.
  - Mutation probe CONFIRMED the idempotence guard is load-bearing: removing it causes the test to fail, restoring it causes it to pass.
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py`

### T3

- **Master directive for this turn**: Global-consistency cross-validation over T1+T2. Verify integration coherence, commit-separability, baselines, forbidden files, W2 surfaces, security, access-control.
- **Principal work**:
  - No source edits. Read-only acceptance grid (7 items).
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - awk+grep: F-7 block (middleware.py:3330-3541) has ZERO hits of `extra_params` — confirmed disjoint mutable state with F-6.
  - Import-graph: F-6 test files import only from `utils.tools`; F-7 test files import only from `utils.sanitize`. No cross-import.
  - `uv run pytest backend/open_webui/test/` — 382 passed / 9 pre-existing failed / 5 pre-existing collection errors (exact match).
  - `bun run check` — 9190 errors (baseline preserved).
  - `uv run ruff format --check` on 6 W3-touched files — exit 0.
  - `git diff` for forbidden files (`utils/tools.py`, `utils/image_analysis.py`, `utils/knowledge_export.py`, `routers/*`, `retrieval/loaders/*`, `src/` production code) — 0 diff each.
  - `git diff` for W2 surfaces (`retrieval.py`, `kg1.py`, `skills.py`) — 0 diff each.
  - `bunx tsc --noEmit --strict --target ES2020 --module ESNext --moduleResolution bundler e2e/tests/admin-rag-settings.spec.ts` — exit 0.
  - 3 kind 2 own security adversarial payloads: nested bidi + null byte sandwich — stripped; tabs/newlines/CR preserved; Cyrillic look-alike file_id + ZWS — ValueError raised. All PASS.
  - 2 kind 2 own access-control adversarial payloads: skill in `user_skill_ids` excluded; agent_skill-type + user-access + not-in-user_skill_ids included. All PASS.
  - Kind 2 independent mutation probe (own payload): removing preamble guard → FAIL; restore → PASS.
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - F-6 (`extra_params` dict) and F-7 (`form_data['messages']`) operate on DISJOINT objects — zero shared mutable state confirmed empirically.
  - Commit-separability confirmed: zero whole-word shared symbols between F-6 hunk (3286-3288) and F-7 hunk (3330-3541).
  - `backend/open_webui/utils/middleware.py:3279` — pre-existing `add_file_context` writes to `form_data['messages']` before F-7 preamble injection runs. Benign because idempotence check is exact-string match on system-role content only. Worth future structural cleanup but pre-existing and out of W3 scope.
  - `src/` and `e2e/` diffs are pre-existing Prettier carryover from W1/W2 commits — not W3 changes.
  - `_SKIP_RAG_MAX_BYTES = 256 * 1024` — single definition confirmed at `sanitize.py:186`.
- **Files touched**: None (read-only validation turn)
- **Cross-turn findings**:
  - T1 replica-pattern anti-pattern (`test_middleware_agent_skill_ids.py`) is a structural debt item, not a correctness defect; pinned to W4 F-9 systemic cleanup.
  - T2-retry mutation probe result (load-bearing idempotence guard) independently re-verified by kind 2 in T3 with own payload — double-confirmed.
  - W2 surface integrity maintained: no regressions introduced by W3 edits.

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

- T2 first attempt: kind 2 classified both the vacuous preamble idempotence test and the dual `_SKIP_RAG_MAX_BYTES` constant as "non-blocker" observations. Kind 3 Mode B re-adjudicated both as blockers: a PASS verdict with false coverage in a security-critical path is a spec violation worse than an explicit FAIL. Required 1 surgical retry. The lesson is that "non-blocker" is not equivalent to "safe to defer" when the observation concerns test validity in a security hardening path.

## What Was Considered But Not Tried (Deferred)

- **Replica test pattern systemic cleanup**: T1 integration test (`test_middleware_agent_skill_ids.py`) reproduces the W1 comprehension-replica anti-pattern. Flagged by kind 2 as non-blocker; pinned by kind 3 to W4 F-9 for systemic cleanup across all affected test files.
- **`add_file_context` at middleware.py:3279 structural refactor**: pre-existing code writes to `form_data['messages']` before F-7's preamble injection. Benign given current idempotence design, but ordering dependency is implicit. Out of W3 scope; deferred for future integration-coherence work.
- **`ERROR_MESSAGES.DEFAULT(str(e))` exception handler leakage pattern**: remains deferred from W2 decision. Not touched in W3.
- **13 pre-existing ruff violations in `kg1.py`**: deferred, unchanged from W2.
- **`RAG_RESEARCH_MODEL` orphan PersistentConfig**: deferred, unchanged from W2.

## What Was Given Up

Nothing.

## Deferred Queue For Replanning

- `backend/open_webui/test/utils/test_middleware_agent_skill_ids.py` — comprehension-replica integration test anti-pattern; recommend W4 F-9 systemic cleanup pass across all affected test files (same pattern appeared in W1 as well)
- `backend/open_webui/utils/middleware.py:3279` — pre-existing `add_file_context` writing to `form_data['messages']` before F-7 preamble injection; implicit ordering dependency; recommend future integration-coherence wave or structural refactor pass
- `backend/open_webui/utils/middleware.py` (exception handlers) — `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern; recommend dedicated security-hardening wave
- `backend/open_webui/kg1.py` — 13 pre-existing ruff violations; recommend low-priority cleanup wave
- `backend/open_webui/utils/middleware.py` (PersistentConfig) — `RAG_RESEARCH_MODEL` orphan; recommend config-audit wave

## Unresolved Findings

None - wave closed clean.

## Files Modified (absolute paths)

- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/sanitize.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_tools_agent_skill_registration.py` (new)
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_middleware_agent_skill_ids.py` (new)
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_sanitize_injected_markdown.py` (new)
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/apps/webui/test_middleware_skip_rag_sanitizer.py` (new)

## Behavioral Verifications Run

- `uv run pytest backend/open_webui/test/` — 382 passed / 9 pre-existing failed / 5 pre-existing collection errors (delta +95 vs W2 pre-wave baseline; failed/errors unchanged throughout all turns)
- `uv run python -c "import open_webui"` — exit 0 (verified after T1 and T2)
- `uv run ruff format --check` on 6 W3-touched files — exit 0 (verified after T2 and T3)
- `bun run check` — 9190 errors (baseline preserved; verified after T1 and T3)
- `bunx tsc --noEmit --strict --target ES2020 --module ESNext --moduleResolution bundler e2e/tests/admin-rag-settings.spec.ts` — exit 0 (verified in T3)
- Kind 2 T2 adversarial probes (15 total): prompt-injection survival, delimiter forgery, file-ID forgery x9 cases, byte cap, multi-byte boundary, preamble idempotence x3 cases, double-injection, empty sanitization, 1 MB regex perf, F-6 contract alignment — all PASS
- Kind 2 T3 own security adversarial payloads (3): nested bidi + null byte sandwich — stripped; tabs/newlines/CR preserved; Cyrillic look-alike `file_id` + ZWS — ValueError raised — all PASS
- Kind 2 T3 own access-control adversarial payloads (2): skill in `user_skill_ids` excluded; agent_skill-type + user-access + not-in-user_skill_ids included — all PASS
- Mutation probe T2-retry (kind 1): `middleware.py:3513` `if not _preamble_already_present` → `if True` — test FAILED; reverted — test PASSED; `git diff` clean
- Mutation probe T3 (kind 2 independent, own payload): same idempotence guard — removing → FAIL, restore → PASS (double-confirmed)
- awk+grep scope isolation: F-7 block (middleware.py:3330-3541) has ZERO hits of `extra_params` — confirmed disjoint state with F-6
- `git diff` forbidden files (`utils/tools.py`, `utils/image_analysis.py`, `utils/knowledge_export.py`, `routers/*`, `retrieval/loaders/*`, `src/` production code) — 0 diff each
- `git diff` W2 surfaces (`retrieval.py`, `kg1.py`, `skills.py`) — 0 diff each

## Wave Summary

W3 closed COMPLETE in 3 turns plus 1 surgical retry (T2-retry). F-6 (C-2) wired `__agent_skill_ids__` symmetrically with `__skill_ids__` in the native-FC `extra_params` dict via a 4-line access-controlled mirror (middleware.py:3270-3273, 4 new tests). F-7 (C-3) delivered full skip_rag prompt-injection and DoS hardening: new pure helper `sanitize_llm_injected_markdown(text, file_id)` in `utils/sanitize.py` strips Unicode bidi/zero-width/C0 control chars, enforces `[a-zA-Z0-9_-]` file_id whitelist (ValueError on invalid), wraps content in authoritative delimiters, and is reusable for any future LLM-context injection path; middleware.py skip_rag block calls the sanitizer, prepends verbatim `_SKIP_RAG_PREAMBLE` (idempotent via exact-string match), emits `skip_rag_truncated` event, raises on double-injection, and caps at 256 KiB UTF-8 bytes (single source of truth at `sanitize.py:186`); 91 new tests (83 unit + 8 REAL integration). Baselines at W3 close: pytest 382 passed / 9 pre-existing failed / 5 pre-existing collection errors; `bun run check` 9190 errors (unchanged); ruff format clean; Playwright TS-strict exit 0; two-commit close ready (F-6 and F-7 independently revertible, zero shared symbols between hunks).

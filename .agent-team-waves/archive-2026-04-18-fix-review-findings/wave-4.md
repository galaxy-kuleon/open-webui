# Wave 4 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Thread per-user credentials through user-triggered LLM calls (F-8/H-1) and extract skip_rag injection into a named, testable pure function (F-9/H-5)
**Turns executed**: 3 + 1 retry (of budget 5)
**Master directives issued**: 6 (T0 plan + Mode A + 4x Mode B)
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-18

## Turn Log

### T1

- **Master directive for this turn**: Execute F-8 credential threading. Modify `_async_llm_completion` to require positional `acting_user: UserModel | None` with no default; remove internal super-admin fallback; update all callers (organizer passes None + INFO log, image_analysis chain threads user); add 4 new tests + update 19 existing tests.
- **Principal work**:
  - `backend/open_webui/utils/knowledge_export.py` — signature changed to required positional `acting_user`; internal admin fallback removed; docstring updated; organizer INFO log `"LLM call under super-admin credential: reason=inbox_organizer"` added
  - `backend/open_webui/utils/image_analysis.py` — `acting_user` threaded through `analyze_image`, `call_vision_llm`, `_run_vision_ocr_fallback`
  - `backend/open_webui/test/utils/test_async_llm_completion_user_threading.py` (new) — 4 tests
  - `backend/open_webui/test/utils/test_knowledge_export_llm_bridge.py` — 19 existing tests updated for new required param
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: FAIL (0 retries — adversarial probe identified critical gap before retry)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 378 passed / 9 failed / 5 errors
  - Adversarial probe: `routers/files.py::process_uploaded_file` calls `analyze_image(...)` without `user=user`; existing `test_file_upload_image_analysis.py` mock accepted `**kwargs` silently, masking the regression
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/routers/files.py:134` — `analyze_image(...)` call lacked `user=user` despite user being in scope at line 98; every production image analysis ran with `user=None` even after correct internal threading
  - `backend/open_webui/test/utils/test_file_upload_image_analysis.py` — mock accepted `**kwargs`, silently swallowing any missing required argument; this is the structural test-design flaw that masked the HTTP entry gap
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/knowledge_export.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/image_analysis.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_async_llm_completion_user_threading.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_knowledge_export_llm_bridge.py`

### T1 retry

- **Extension reason**: HTTP entry point `routers/files.py:134` missed in first attempt; Mode B REDIRECT issued to extend F-8 scope to include the router and its test.
- **Master directive for this turn**: Fix `files.py:134` to pass `user=user`; rewrite `test_file_upload_image_analysis.py` mock as REQUIRED POSITIONAL (no `**kwargs`) so omission raises TypeError; add assertion `assert call['user'] is acting_user`; re-run adversarial sentinel probe.
- **Principal work**:
  - `backend/open_webui/routers/files.py:134` — `user=user` added to `analyze_image(...)` call
  - `backend/open_webui/test/utils/test_file_upload_image_analysis.py` — mock parameter changed from `**kwargs` to required positional `user`; added assertion `assert call['user'] is acting_user`; sentinel probe: `captured.user.id = 'probe-user-SENTINEL'` confirmed propagation end-to-end
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 1 retry)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 378 passed / 9 failed / 5 errors (baseline preserved)
  - Synthetic mutation probe: omitting `user=` in files.py call → TypeError confirmed (not silent swallow)
  - Broader caller scan: all `analyze_image` + `call_vision_llm` call sites confirmed threading user correctly
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - Confirmed: F-8 behavioural guarantee (user credentials end-to-end) satisfied only after HTTP entry fixed
  - Pattern recorded: transformation-boundary fixes must include the ENTRY point(s), not only the internals
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/files.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_file_upload_image_analysis.py`

### T2

- **Master directive for this turn**: Execute F-9. Create `utils/skip_rag.py` with frozen `SkipRagContext` dataclass (6 fields) + pure `build_skip_rag_context` function (no I/O inside). Shrink middleware.py skip_rag region by replacing ~210 LOC with single call + field reads. Relocate `_SKIP_RAG_PREAMBLE` as SoT to skip_rag.py; alias re-export at middleware.py. Rewrite `test_skip_rag_injection.py` to call real function (delete inline replica). Add anti-spoofing test verified by mutation probe.
- **Principal work**:
  - `backend/open_webui/utils/skip_rag.py` (new, 281 LOC) — `SkipRagContext` frozen dataclass with 6 fields (`context_block`, `preamble_needed`, `truncation_events`, `double_injection_applied`, `server_side_filename_used`, `sources`); `build_skip_rag_context` pure function; `_SKIP_RAG_PREAMBLE` defined at line 42 as SoT
  - `backend/open_webui/utils/middleware.py` — skip_rag region: ~210 LOC → ~60 LOC (−150 LOC net); import added; `_SKIP_RAG_PREAMBLE` at line 162 as alias re-export (not string copy); single call to `build_skip_rag_context(...)`
  - `backend/open_webui/test/utils/test_skip_rag_injection.py` — rewritten: 44 tests (up from ~27 in prior replica-based version); all tests call real `build_skip_rag_context`; inline replica deleted; new `test_skip_rag_respects_server_side_filename_for_extension` asserts `server_side_filename_used is True` + docling invoked
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (0 retries)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 405 passed / 9 failed / 5 errors (+27 vs W3 baseline of 378; failed/errors unchanged)
  - Frozen dataclass probe: `SkipRagContext` field assignment → FrozenInstanceError confirmed
  - Purity probe: confirmed no `event_emitter` call inside `build_skip_rag_context`
  - Anti-spoofing mutation probe: inverting server/client ext precedence → test FAIL; reverting → PASS + git clean
  - Import cycle check: `skip_rag.py` has no import from `middleware.py` confirmed
  - SoT checks: `_SKIP_RAG_MAX_BYTES` — 1 definition at `sanitize.py:186`; `_SKIP_RAG_PREAMBLE` — 1 definition at `skip_rag.py:42`, 1 alias re-export at `middleware.py:162`
  - W3 `test_middleware_skip_rag_sanitizer.py` — 8/8 PASS byte-identical
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/utils/skip_rag.py:42` — `_SKIP_RAG_PREAMBLE` is now the canonical prompt-injection defense template (single SoT)
  - `backend/open_webui/utils/skip_rag.py` — 4 `ruff check` violations: F401 (unused `field` import), I001 (import sort), UP035 (Callable source), C901 (complexity 17 > 10); consistent with project-wide pre-existing ruff debt; `ruff format --check` still clean
  - `SkipRagContext.truncation_events` and `.sources` are `list[dict]` — frozen prevents reassignment but not in-place mutation; current caller does not mutate in place
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skip_rag.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_skip_rag_injection.py`

### T3

- **Master directive for this turn**: Global-consistency cross-validation. Execute 7-item acceptance grid: (1) pytest baseline, (2) bun check baseline, (3) ruff format clean on all 9 W4-touched files, (4) F-8 adversarial mutation probe, (5) F-9 anti-spoofing mutation probe, (6) commit-separability (F-8 ∩ F-9 = ∅), (7) W3 sanitizer byte-identical regression.
- **Principal work**:
  - All 7 acceptance grid items executed; evaluator corrected one minor principal report error (bun check count 9191 → 9190 actual)
  - Commit-separability empirically confirmed: F-8 and F-9 file sets are disjoint; no shared files between the two units
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (0 retries; corrected bun count in report)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 405 passed / 9 failed / 5 errors
  - `bun run check` — 9190 errors (baseline preserved; principal mis-reported 9191)
  - `uv run ruff format --check` on all 9 W4-touched files — exit 0 (clean)
  - F-8 adversarial mutation probe (TypeError on omitted `user=`) — confirmed
  - F-9 anti-spoofing mutation probe (ext precedence inversion) — FAIL on inversion, PASS + git clean on revert
  - `git diff --name-only HEAD~2..HEAD~1` and `git diff --name-only HEAD~1..HEAD` — F-8 ∩ F-9 = ∅ confirmed
  - W3 `test_middleware_skip_rag_sanitizer.py` — 8/8 PASS byte-identical
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - Minor: principal mis-reported bun check count as 9191; actual 9190; no meaningful regression, baseline held
- **Cross-turn findings**:
  - F-8 pattern: credential-threading fixes must always be validated at the HTTP entry boundary, not just at internal call sites; `**kwargs`-style test mocks silently mask missing required arguments and must not be used for required credentials
  - F-9 pattern: `SkipRagContext` frozen dataclass is the reusable template for future `build_*_context` extractions; re-export alias (not string copy) for SoT constants enforces single source without circular import risk
  - 4 ruff check violations in `skip_rag.py` are pre-existing-debt-consistent; F401+I001+UP035 are trivial W5 cleanups; C901 complexity requires structural refactor
  - `SkipRagContext` list fields are frozen-against-reassignment but not against in-place mutation — current callers are safe; noted for W5 hardening

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

- T1 first attempt: threaded `acting_user` correctly through `knowledge_export.py` + `image_analysis.py` internals but did not reach `routers/files.py:134`, which is the actual HTTP entry point where `analyze_image` is called from production request handlers. Every production image analysis ran with `user=None` despite the internally correct threading. The test for this code path used `**kwargs` in the mock, which silently accepted missing required arguments and masked the gap entirely. Adversarial probe by the evaluator surfaced the gap before the wave could close incorrectly.

## What Was Considered But Not Tried (Deferred)

- Formal Python type annotation for `acting_user` parameter in `knowledge_export.py` (currently comment-style only) — W5 polish candidate
- Deeper immutability for `SkipRagContext.truncation_events` and `.sources` (tuple or `Mapping` instead of `list[dict]`) — W5 polish candidate; current callers do not mutate in place so this is safe-to-defer
- 4 `ruff check` violations in `backend/open_webui/utils/skip_rag.py`: F401 (unused `field` import), I001 (import sort order), UP035 (`Callable` import source), C901 (function complexity 17 > 10) — F401+I001+UP035 are trivial one-liner cleanups for W5; C901 would require factoring out sub-functions
- Pre-existing deferred items carried from W3 (not re-opened in W4): `ERROR_MESSAGES.DEFAULT(str(e))` pattern, `RAG_RESEARCH_MODEL` orphan, 13 pre-existing ruff violations in `kg1.py`, `add_file_context` structural coupling

## What Was Given Up

Nothing.

## Deferred Queue For Replanning

- `backend/open_webui/utils/skip_rag.py` — F401/I001/UP035 ruff violations (trivial): recommended for W5 polish pass
- `backend/open_webui/utils/skip_rag.py` — C901 complexity 17 > 10: recommended for W5 polish if time allows; requires sub-function extraction
- `backend/open_webui/utils/skip_rag.py` — `SkipRagContext.truncation_events` / `.sources` in-place mutation exposure: recommended for W5 hardening (change to `tuple[dict, ...]` or `Mapping`)
- `backend/open_webui/utils/knowledge_export.py` — `acting_user` formal type annotation: recommended for W5 polish (1-line change)
- Pre-existing from W3: `ERROR_MESSAGES.DEFAULT(str(e))` pattern, `RAG_RESEARCH_MODEL` orphan, 13 pre-existing ruff violations in `kg1.py`, `add_file_context` structural coupling — carry to W5 or post-wave cleanup

## Unresolved Findings

None - wave closed clean.

## Files Modified (absolute paths)

**Unit A — F-8 (credential threading):**
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/knowledge_export.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/image_analysis.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/routers/files.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_async_llm_completion_user_threading.py` (new)
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_file_upload_image_analysis.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_knowledge_export_llm_bridge.py`

**Unit B — F-9 (skip_rag extraction):**
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/skip_rag.py` (new, 281 LOC)
- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_skip_rag_injection.py`

Total: 7 modified + 2 new = 9 files. Unit A ∩ Unit B = ∅.

## Behavioral Verifications Run

- `uv run pytest backend/open_webui/test/` — 405 passed / 9 pre-existing failed / 5 pre-existing collection errors. Delta: +31 pass vs W3 baseline 374 (T1 baseline 378; +27 from T2 rewrite). Failed/errors count unchanged from pre-wave baseline.
- `uv run python -c "import open_webui"` — exit 0 (no import cycle)
- `uv run ruff format --check` on all 9 W4-touched files — exit 0 (clean)
- `bun run check` — 9190 errors (baseline preserved exactly; principal mis-reported 9191 in T3, corrected by evaluator)
- `uv run pytest backend/open_webui/test/utils/test_middleware_skip_rag_sanitizer.py` — 8/8 PASS byte-identical (W3 sanitizer regression guard held)
- Adversarial probe F-8 (synthetic mutation): removed `user=user` from `files.py:134` → TypeError raised (not silent kwarg swallow); test fails correctly
- Adversarial probe F-8 (sentinel propagation): `captured.user.id = 'probe-user-SENTINEL'` confirmed end-to-end through `analyze_image` → `call_vision_llm`
- Adversarial probe F-9 (anti-spoofing mutation): inverted server/client ext precedence in `build_skip_rag_context` → `test_skip_rag_respects_server_side_filename_for_extension` FAIL; reverted → PASS + git clean
- Probe F-9 (frozen dataclass): `SkipRagContext` field assignment after construction → `FrozenInstanceError` raised
- Probe F-9 (purity): `build_skip_rag_context` confirmed no `event_emitter` call inside function body
- Import cycle check: `skip_rag.py` has no import from `middleware.py` — no cycle
- SoT check `_SKIP_RAG_MAX_BYTES`: 1 definition at `sanitize.py:186`, 0 elsewhere
- SoT check `_SKIP_RAG_PREAMBLE`: 1 definition at `skip_rag.py:42`, 1 alias re-export at `middleware.py:162`, 0 independent copies
- Caller breadth scan: all `analyze_image` + `call_vision_llm` call sites confirmed threading user correctly post-T1-retry
- Commit-separability: `git diff --name-only` across F-8 and F-9 commits — file sets disjoint, F-8 ∩ F-9 = ∅ empirically confirmed

## Wave Summary

W4 closed COMPLETE with 3 turns plus 1 retry (T1 retry required after adversarial evaluation surfaced a missed HTTP entry point at `routers/files.py:134`). F-8 (H-1) is fully satisfied: `_async_llm_completion` now takes a required positional `acting_user` with no internal admin fallback; the entire image analysis call chain (router → `analyze_image` → `call_vision_llm` → `_run_vision_ocr_fallback` → `_async_llm_completion`) threads the authenticated user end-to-end, and the organizer path explicitly passes `None` with an INFO-level audit log. F-9 (H-5) is fully satisfied: `utils/skip_rag.py` houses a frozen `SkipRagContext` dataclass (6 named fields) and a pure, testable `build_skip_rag_context` function; middleware.py's skip_rag region shrank by ~150 LOC; the anti-spoofing test is mutation-verified and calls the real function with no inline replica. Baselines at W4 close: pytest 405/9/5, bun check 9190, ruff format clean, W3 sanitizer 8/8 byte-identical; W5 scope (F-10 + F-11) is disjoint from all W4 surfaces.

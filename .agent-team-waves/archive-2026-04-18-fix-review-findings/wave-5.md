# Wave 5 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Fix silent-failure and resource-exhaustion defects — F-10 (H-2) token-cascade Tier-3 silent failure + F-11 (H-7) `generate_document_index` DB session leak and static timeout not enforced
**Turns executed**: 3 (of budget 5) — T1, T2 (with 1 retry), T3
**Master directives issued**: 5 (Mode A at T0; Mode B at T1-PASS; Mode B at T2-FAIL with REDIRECT; Mode B at T2-retry-PASS; T3 implicit close)
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-18

## Turn Log

### T1

- **Master directive for this turn**: F-10 cascade hardening — sentinel return on exception path; Tier-3 post-gather re-measure with structured status events; 5 new tests.
- **Principal work**:
  - `backend/open_webui/utils/middleware.py`: `extract_relevant_content_from_document` exception path changed to return `None` sentinel (previously returned original `document_content`, hiding failure). Tier-3 gather loop re-measures `estimate_sources_total_tokens` post-gather; if over-budget emits `token_cascade_failed` status event and returns `[]`; partial failures emit `token_cascade_partial` with `success_map` + `failure_map`. Event shapes reuse existing `{type: 'status', data: {action, done, description, ...}}` envelope.
  - `backend/open_webui/test/utils/test_token_cascade_tier3_failure.py` (new): 5 tests covering all-succeed, all-fail, partial-fail, over-budget-all-success, sentinel-on-exception.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 410 passed / 9 pre-existing failed / 5 pre-existing collection errors (baseline delta +5 from 405)
  - 6 adversarial probes: sentinel path, all-fail, partial-fail, over-budget-all-success, empty cascade, all-exception edge case
  - Mutation probe on empty-maps guard (verified load-bearing: mutation causes `test_all_exception_emits_only_failed_event_no_partial` to FAIL)
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - Non-blocker 1: Stale docstring at `middleware.py:2207-2208` — "Falls back to original content" no longer true after sentinel change.
  - Non-blocker 2: Return annotation `-> str` should be `-> str | None` at same function.
  - Non-blocker 3: All-Exception path emits `token_cascade_partial` with empty maps before `token_cascade_failed` fires — empty-maps telemetry noise (later fixed in T2 with empty-maps guard).
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_token_cascade_tier3_failure.py`

### T2

- **Master directive for this turn**: F-11 DB session + timeout + fold 3 T1 non-blockers into T2 under shared-patterns lens (authorized by kind 3 Mode B).
- **Principal work** (first attempt — FAILED evaluator):
  - `backend/open_webui/routers/retrieval.py`: Named constants `PER_CHUNK_TIMEOUT_SECONDS = 120`, `MAX_TOTAL_TIMEOUT_SECONDS = 1800` at module top. `db.close()` before `run_coroutine_threadsafe` + executor dispatch. `future.cancel()` in `finally` block with DEBUG log on cancel-False. WARN logs for dispatch config + per-chunk wall-clock on exit. Sentinel `return None` on TimeoutError AND general Exception paths. `document_index_dispatch_timeout` + `document_index_dispatch_failed` events. Caller audit: only 1 production caller at `retrieval.py:2324`, handles None via `if index_content:` guards.
  - `backend/open_webui/utils/middleware.py` (T1 alignments): docstring fix "Falls back to original content" → "Returns None sentinel on failure"; return annotation `-> str` → `-> str | None`; empty-maps guard `has_any_map_data = bool(success_map) or bool(failure_map)` suppresses partial event when all maps empty.
  - 7 F-11 tests + 1 F-10 guard test added.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: FAIL (0 retries; REDIRECT issued by kind 3 Mode B)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 410 passed baseline confirmed
  - Adversarial probe: 100-chunk simulation at 119.9s each → projected ~12000s actual vs 1800s claimed cap
  - Critical finding: `total_timeout` computed and logged but NEVER passed as deadline to per-chunk calls; only `per_chunk_timeout=120s` passed; cap was documentation-only
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - BLOCKER at `retrieval.py` (multi-chunk for-loop): `total_timeout` derived and logged but not enforced as a deadline; 100 chunks × 119.9s each = ~12000s actual vs 1800s cap. Fix pattern: capture `deadline = time.monotonic() + total_timeout` before loop; compute `remaining = deadline - time.monotonic()` per iteration; pass `effective_timeout = min(per_chunk_timeout, remaining)`.
  - Non-blocker: DB session leak if `_split_text_by_tokens` raises before `db.close()` — unreachable in production because caller does not pass `db=`, but future-exposure risk.
  - Non-blocker: Middleware docstring over-claims "Returns None sentinel on failure" but `middleware.py:2219` and `middleware.py:2223` still return `document_content` in non-exception degenerate cases (empty streaming, unrecognized response format). Opposite direction from T1 non-blocker #1.
- **Files touched** (first attempt only — changes superseded by retry):
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/retrieval.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`

**T2 Retry** (kind 3 Mode B REDIRECT — 3 surgical fixes):

- **Principal work**:
  - Fix 1 (BLOCKER) — `backend/open_webui/routers/retrieval.py` deadline enforcement: `deadline = _time.monotonic() + total_timeout` at line 2038 before loop; `remaining = deadline - _time.monotonic()` at loop-top (line 2043); `remaining <= 0` branch (lines 2044-2064) emits WARN + `document_index_dispatch_timeout` event + `return None`; `effective_timeout = min(per_chunk_timeout, remaining)` at line 2065 passed as `timeout=effective_timeout` at line 2074.
  - Fix 2 — `backend/open_webui/utils/middleware.py:2182-2192` precise docstring distinguishing None-sentinel paths (exceptions) from `document_content`-return paths (empty streaming at line 2219, unrecognized response format at line 2223).
  - Fix 3 — `backend/open_webui/routers/retrieval.py` try/finally: outer `try:` at line 1984 wrapping `_split_text_by_tokens` through end of function; `db_closed = True` flag at line 1995 on happy-path close; `finally:` at lines 2140-2143: `if db is not None and not db_closed: db.close()` — prevents double-close on happy path, ensures close on exception path.
  - New test `test_total_timeout_exhaustion_returns_sentinel` in `test_document_index_db_session.py`: monkeypatches `_time.monotonic` to advance 119.9s per chunk; asserts loop exits after ~16 chunks (1918.4s simulated vs 11990s for 100 chunks).
- **Evaluator verdict**: PASS (1 retry total for T2)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 411 passed / 9 pre-existing failed / 5 pre-existing collection errors
  - Realistic mock probe: LLM mock raises `FuturesTimeoutError` when simulated LLM time > timeout parameter. Under realistic mock, production caps at exactly 1800.0s (chunk 16 gets `effective_timeout=1.5s`; raises TimeoutError after 1.5s; exits via `FuturesTimeoutError` branch). The 1918.4s under kind 1's mock is test-mock artifact; production enforcement is tight.
  - try/finally mutation probe: 3 sub-cases (exception before close, happy path, exception after close) — all PASS.
  - 8 tests in `test_document_index_db_session.py` — all pass.
- **Key findings**:
  - "Computed and logged but not enforced" is a subtle class of silent failure. A timeout/limit/budget value that is derived and logged but not passed to the enforcement point (future.result timeout, loop guard, allocator call) constitutes false advertising.
- **Files touched** (retry — canonical):
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/retrieval.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_document_index_db_session.py` (new)

### T3

- **Cross-turn findings**: 6-section cross-validation report (report-only; no source changes).
- **Master directive for this turn**: T3 = global-consistency cross-validation. Unit C polish DENIED — T3 is not scope-expansion; skip_rag.py + knowledge_export.py were not W5's behavioural surface and must go through dedicated evaluation if addressed.
- **Principal work**:
  - Section 1 — Integration coherence: F-10 (middleware.py, `apply_token_budget_cascade`) and F-11 (retrieval.py, `generate_document_index`) are INDEPENDENT call-graph branches. middleware.py imports only `SearchForm` + `process_web_search` from retrieval.py (not `generate_document_index`). retrieval.py imports nothing from middleware.py. F-10 operates on read/inference path; F-11 on write/ingestion path. No intersection.
  - Section 2 — Shared-patterns confirmation: Both use sentinel-return + event-emission discipline. F-10 sentinels are list-typed (`[]`, `index_only`, `sources`); F-11 sentinel is `None`. Events use consistent `{type: 'status', data: {action, description, done, ...}}` envelope. `chunks_completed` asymmetry between FuturesTimeoutError branch and deadline branch is intentional (different failure modes).
  - Section 3 — Commit-separability: Commit 1 (F-10 + T1 alignments): middleware.py + test_token_cascade_tier3_failure.py. Commit 2 (F-11 + try/finally + wave artefacts): retrieval.py + test_document_index_db_session.py + wave-5.md + wave-5-reveal.md. Zero file overlap confirmed.
  - Section 4 — Baselines preserved: pytest 411/9/5, bun 9190, ruff 12 pre-existing violations, no new violations.
  - Section 5 — Forbidden files untouched: zero diff across sanitize.py, tools.py, routers/skills.py, kg1.py, image_analysis.py, skip_rag.py, knowledge_export.py. src/ diff is pre-existing W1/W2 formatter cleanup.
  - Section 6 — Mutation-probe re-reference: F-10 empty-maps guard load-bearing (mutation causes test_all_exception_emits_only_failed_event_no_partial to FAIL). F-11 deadline check load-bearing (mutation causes 30-chunk loop to run 3600s vs 1800s cap).
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries) — final single-line statement: "W5 ready to close: 2 commits, baselines preserved, ledger empty."
- **Evaluator commands run**:
  - 9 independent cross-validation verifications across all 6 sections
  - Call-graph independence verified empirically (import analysis)
  - Commit-set file-overlap check — confirmed disjoint
  - Forbidden-file diff — confirmed empty
  - 14/14 new W5 tests confirmed passing
  - 411/9 baseline confirmed held
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `FuturesTimeoutError` branch emits `chunk_count` but not `chunks_completed`; deadline-check branch emits both — minor event-payload asymmetry, non-functional.
  - `elapsed` microsecond imprecision in deadline path — sub-perceptible, no behavioural impact.
- **Files touched**: None (report-only)

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

- T2 first attempt computed `MAX_TOTAL_TIMEOUT_SECONDS = 1800` and logged it but passed only `per_chunk_timeout=120s` to each `_call_index_llm` invocation. The 1800s cap was documentation-only — a silent failure of the safety constraint itself. Kind 2's adversarial probe exposed this: 100 chunks × 119.9s = ~12000s actual vs 1800s claimed. Fixed in T2 retry by computing `deadline = _time.monotonic() + total_timeout` before the loop and clamping `effective_timeout = min(per_chunk_timeout, remaining)` per iteration.
- Pattern enshrined: "computed and logged but not enforced" is a repeatable class of silent failure. Whenever a timeout/limit/budget is derived, verify it is PASSED to the enforcement point (future.result timeout, loop guard, allocator call). Logging alone is false advertising.

## What Was Considered But Not Tried (Deferred)

**Unit C discretionary polish** — DENIED by kind 3 Mode B during T3 directive (T3 is cross-validation, not scope-expansion; skip_rag.py + knowledge_export.py were not W5's behavioural surface):
- F401 unused `field` import in `backend/open_webui/routers/skip_rag.py` (trivial auto-fix).
- I001 import sort in `backend/open_webui/routers/skip_rag.py` (trivial).
- UP035 `typing.Callable` → `collections.abc.Callable` in `backend/open_webui/routers/skip_rag.py` (trivial).
- Formal `acting_user: UserModel | None` Python type annotation in `backend/open_webui/routers/knowledge_export.py` (currently comment-style).

**Pre-existing items never in /atw run scope:**
- `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern — cross-cutting, affects many files.
- `RAG_RESEARCH_MODEL` orphan PersistentConfig from W1.
- 13 pre-existing ruff violations in kg1.py from W2 baseline.
- `add_file_context` at `middleware.py:3279` structural coupling observation (W3 observation).
- `SkipRagContext` deeper immutability (tuple/Mapping for list fields) from W4.
- C901 complexity 16 on `generate_document_index` — pre-existing from W4.

**New non-blocker observations surfaced in W5 T2/T3:**
- `FuturesTimeoutError` branch emits `chunk_count` but not `chunks_completed`; deadline-check branch emits both — minor event-payload asymmetry, non-functional.
- `elapsed` microsecond imprecision in deadline path — sub-perceptible, no behavioural impact.

## What Was Given Up

Nothing. W5 closed all 2 assigned findings fully with 1 surgical retry on T2. The REDIRECT recovery rung was used once.

## Deferred Queue For Replanning

None — W5 is the final wave of this /atw run. Items below are queued for user post-run triage, not a future wave:

- `backend/open_webui/routers/skip_rag.py`: F401 unused `field` import, I001 import sort, UP035 `typing.Callable` modernization — user may close in a post-/atw commit.
- `backend/open_webui/routers/knowledge_export.py`: formal type annotation for `acting_user` parameter — user may close in a post-/atw commit.
- `backend/open_webui/routers/retrieval.py`: C901 complexity on `generate_document_index` (pre-existing from W4 baseline) — not a W5 addition.
- `backend/open_webui/utils/middleware.py:3279`: `add_file_context` structural coupling observation (carried from W3, never assigned to a wave).
- Cross-cutting `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern — requires a dedicated cross-cutting wave if addressed.

## Unresolved Findings

None - wave closed clean. All 4-6 hygiene items catalogued in the deferred section above are non-blockers. No blocker was left open at wave close.

## Files Modified (absolute paths)

- `/Users/noelbao/Works/open-webui/backend/open_webui/utils/middleware.py` — sentinel return on exception; Tier-3 post-gather re-measure + `token_cascade_failed`/`token_cascade_partial` events; empty-maps guard; docstring precision distinguishing None-sentinel paths from `document_content`-return paths; return annotation `-> str | None`
- `/Users/noelbao/Works/open-webui/backend/open_webui/routers/retrieval.py` — named timeout constants; `db.close()` before executor dispatch; `deadline = _time.monotonic() + total_timeout` before loop; `remaining` + `effective_timeout = min(per_chunk_timeout, remaining)` per iteration; `remaining <= 0` early-exit with event + `return None`; `future.cancel()` in finally + DEBUG log; WARN logs entry + exit; sentinel `return None` + `document_index_dispatch_timeout` + `document_index_dispatch_failed` events; try/finally with `db_closed` flag
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_token_cascade_tier3_failure.py` (new) — 5 T1 tests + 1 empty-maps guard test = 6 tests
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_document_index_db_session.py` (new) — 7 F-11 tests + 1 deadline-enforcement test = 8 tests
- `/Users/noelbao/Works/open-webui/.agent-team-waves/wave-5-reveal.md` (new)
- `/Users/noelbao/Works/open-webui/.agent-team-waves/wave-5.md` (this file)

Total: 2 source files modified + 2 new test modules + 14 new tests + 2 wave artefacts. Zero forbidden files touched.

## Behavioral Verifications Run

- `uv run pytest backend/open_webui/test/` — T1: 410 passed / 9 pre-existing failed / 5 pre-existing collection errors; T2-retry: 411 passed / 9 / 5 (delta +1 for total deadline-enforcement test; 14 total new W5 tests across both test modules)
- `uv run python -c "import open_webui"` — exit 0 (verified through T1/T2 turns)
- `uv run ruff format --check` on W5-touched files — exit 0
- `uv run ruff check backend/open_webui/routers/retrieval.py` — 12 pre-existing violations, no new violations
- `bun run check` — 9190 errors (baseline preserved; no new errors)
- T1 adversarial probes (kind 2): 6 probes covering sentinel path, all-fail, partial-fail, over-budget-all-success, empty cascade, all-exception edge case — all PASS
- T1 mutation probe: empty-maps guard verified load-bearing (`test_all_exception_emits_only_failed_event_no_partial` FAILS on mutation)
- T2 first-attempt adversarial probe: 100-chunk simulation at 119.9s/chunk exposed `total_timeout` as documentation-only (projected ~12000s actual vs 1800s cap) — FAIL finding
- T2-retry realistic-mock probe: LLM mock raises `FuturesTimeoutError` when simulated time > timeout parameter; production enforces exactly 1800.0s (chunk 16 gets `effective_timeout=1.5s`, exits via FuturesTimeoutError) — PASS
- T2-retry try/finally mutation probe: 3 sub-cases (exception before close, happy path, exception after close) — all PASS
- T3 cross-validation: 9 independent verifications across 6 sections — all PASS; call-graph independence verified empirically; commit-set file-overlap confirmed disjoint; forbidden-file diff confirmed empty; 14/14 new tests confirmed passing

## Wave Summary

W5 closed COMPLETE in 3 turns (T1, T2+retry, T3). F-10 (H-2) hardened token-cascade Tier-3 against silent failure: sentinel return on exception, post-gather re-measure with `token_cascade_failed`/`token_cascade_partial` events, empty-maps guard preventing spurious telemetry. F-11 (H-7) hardened `generate_document_index` resource management: named timeout constants, DB session released before executor dispatch, TRUE deadline enforcement via `_time.monotonic()` + `remaining` + `effective_timeout` clamp (the critical gap caught by T2's FAIL: `total_timeout` was computed and logged but not passed to per-chunk calls, making the 1800s cap documentation-only), `future.cancel()` in finally, sentinel `return None` + two structured events, try/finally with `db_closed` flag for no-leak/no-double-close across all exit paths. The shared pattern enshrined across W5 (consistent with W4 precedents): async-boundary failure handling = sentinel return + structured status event + explicit cleanup in `finally`. This is the FINAL wave: all 11 Critical+High findings from the 2026-04-18 forensic review are addressed across W1–W5; 14 new tests, 2 source files modified, baselines held at pytest 411/9/5, bun 9190, ruff format clean on all W5-touched files.

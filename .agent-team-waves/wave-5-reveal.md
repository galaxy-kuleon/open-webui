# Wave 5 - Reveal Packet

**Wave Objective**: fix two silent-failure / resource-exhaustion defects in already-shipped code paths — F-10 (H-2) token-cascade Tier-3 silent failure + F-11 (H-7) `generate_document_index` holds DB session up to 10 minutes with static timeout.
**Data-flow segment**: error-path / resource-management
**Blast radius**: largest in this run (DB session lifecycle + cascade fallback correctness under partial failure)
**Total waves**: 5 of 5 (final)

## Spec Slice (from fix-review-findings-spec.md)

---

### F-10 (H-2) — Token-cascade Tier 3 silent failure on gather exceptions

**Severity**: High
**Blast radius**: medium (middleware cascade logic)
**Files in scope**:

- `backend/open_webui/utils/middleware.py` lines ~2118-2126 (Tier 3 extraction loop) and ~2180-2182 (`extract_relevant_content_from_document` silent-return fallback)

**Note**: line numbers shifted post-W3/W4 — re-verify at execution.

**Observation from review**: `asyncio.gather(..., return_exceptions=True)` + per-exception `continue` + early-return of `extracted_sources` means:

1. All-exceptions → returns Tier-1 over-budget content as if Tier-3 succeeded.
2. Partial-exceptions → failed docs retain full Tier-1 content while claiming Tier-3.
3. `extract_relevant_content_from_document` returns the original on its own exception, indistinguishable from success.

**Behavioural requirement**: when Tier-3 extraction fails for any document, the cascade either (a) completes within budget by falling back to Tier-2 index-only, or (b) emits a visible error status and returns an empty list.

**Technical constraints**:

1. After the `gather` loop, **re-measure** `estimate_sources_total_tokens(extracted_sources)`.
2. If still `> max_tokens`:
   - Try Tier-2 fallback (index-only): re-run the loop requesting index summaries only.
   - If Tier-2 also exceeds budget: emit a status event `token_cascade_failed` with details, return `[]`.
3. When `extract_relevant_content_from_document` fails internally, return a sentinel (e.g., `None`) that Tier-3 recognises as "failed for this doc", rather than returning the original and hiding the failure.
4. Partial-success is a valid state: emit a status event `token_cascade_partial` with a per-doc success/failure map so the user sees "2 of 5 documents truncated due to extraction failure".

**Acceptance criteria**:

- Unit test: all-mock-fails scenario → status event emitted + `[]` returned.
- Unit test: partial-mock-fails scenario → status event emitted + only successfully-extracted docs included.
- Unit test: all-success scenario → behaviour unchanged vs current.
- Integration does not regress token-budget semantics for the all-success path.

**Non-deferrable**: yes.

---

### F-11 (H-7) — `generate_document_index` holds DB session for up to 10 minutes

**Severity**: High
**Blast radius**: largest in this run (touches DB session management + LLM call orchestration)
**Files in scope**:

- `backend/open_webui/routers/retrieval.py::generate_document_index` lines ~353-460 (at review time; re-verify)
- `backend/open_webui/routers/retrieval.py::process_file` (caller — verify DB session scope)

**Behavioural requirements**:

1. The DB session MUST be released **before** entering the `run_coroutine_threadsafe` + `future.result(timeout=...)` block. Re-fetch objects by ID after the LLM call if needed.
2. The timeout MUST be dynamic, not a static 600s:
   - Per-chunk timeout: `min(120s per chunk, 600s hard ceiling)`.
   - Total wall-clock budget: `len(chunks) * per_chunk_timeout` but hard-capped at `1800s` (30 min).
3. On exception (including `TimeoutError`), call `future.cancel()` in a `finally` block so the scheduled coroutine doesn't leak.
4. Log at WARN level the timeout configuration used and the wall-clock spent on each chunk so operators can tune.

**Acceptance criteria**:

- Unit test (with mocked `generate_chat_completion`): verifies DB session is closed before LLM call begins (inspect with a session-lifetime tracker).
- Unit test: verifies `future.cancel()` is called on timeout path.
- Unit test: verifies dynamic timeout calculation (small doc → small budget; huge doc → capped at 30 min).
- Existing happy-path document indexing succeeds unchanged.

**Non-deferrable**: yes. Production-grade DB-exhaustion vector.

---

## Deferred Items Assigned To This Wave

None from prior waves carried as blockers. W5 is the last wave; any unresolved items at close are for the user to triage post-run.

## W5 discretionary polish candidates (NOT obligated, but allowed if scope permits)

From prior wave observations:
1. Formal `acting_user: UserModel | None` Python type annotation in `_async_llm_completion` (from W4 T1 non-blocker).
2. `SkipRagContext.truncation_events` / `.sources` deeper immutability (from W4 T2 non-blocker).
3. F401 unused `field` import in skip_rag.py (from W4 T3 non-blocker, trivial).
4. I001 import sort in skip_rag.py (trivial).
5. UP035 Callable import source in skip_rag.py (trivial).

These are DISCRETIONARY. Fix only if T3 budget permits without risking F-10/F-11. Do NOT descope any F-10/F-11 acceptance criterion to accommodate them.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/utils/middleware.py` — F-10 cascade logic + `extract_relevant_content_from_document` signature if changed for sentinel pattern.
  - `backend/open_webui/routers/retrieval.py` — F-11 `generate_document_index` + `process_file` DB session management (READ-ONLY if uncertain; edit only what's required).
  - New test files:
    - `backend/open_webui/test/utils/test_token_cascade_tier3_failure.py` (F-10)
    - `backend/open_webui/test/routers/test_document_index_db_session.py` (F-11)
  - W5 discretionary polish files if addressing those items: `utils/knowledge_export.py`, `utils/skip_rag.py` — ONLY for the specific nit fixes listed above, nothing else.

- **Forbidden files**:
  - Everything under `.agent-team-waves/archive-*/`
  - `backend/open_webui/utils/sanitize.py` (W3, READ-ONLY — `sanitize_llm_injected_markdown` may be called but not modified)
  - `backend/open_webui/utils/tools.py` (W3, READ-ONLY)
  - `backend/open_webui/routers/skills.py` (W2)
  - `backend/open_webui/retrieval/loaders/kg1.py` (W2)
  - `backend/open_webui/utils/image_analysis.py` (W4 — do not re-edit the credential chain)
  - `backend/open_webui/utils/skip_rag.py` (W4 — EXCEPT for discretionary polish items 3-5 listed above, which are trivial imports-only edits)
  - All `src/` frontend code
  - All W1/W2/W3/W4 NEW test files (except where discretionary polish specifically allows — e.g., do not touch `test_skills_upload.py`, `test_validate_admin_dir.py`, `test_retrieval_config.py`, `test_kg1_drain.py`, `test_sanitize_injected_markdown.py`, `test_middleware_skip_rag_sanitizer.py`, `test_tools_agent_skill_registration.py`, `test_middleware_agent_skill_ids.py`, `test_async_llm_completion_user_threading.py`, `test_file_upload_image_analysis.py`, `test_skip_rag_injection.py`)

- **Out-of-scope items**:
  - `RAG_RESEARCH_MODEL` orphan PersistentConfig.
  - Pre-existing `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern.
  - 13 pre-existing ruff violations in `kg1.py`.
  - `add_file_context` at middleware.py:3279 structural coupling observation.
  - Any upstream PR preparation (fork is private).

## Handoff from Wave 4

W4 closed COMPLETE across 2 commits:
- `22c1d00c0` — Unit A (F-8 credential threading): +699/-564, 6 files.
- `9b87ca2e8` — Unit B (F-9 skip_rag extraction): +1281/-532, 5 files.

State at W4 close:
- pytest: 405 passed / 9 pre-existing failed / 5 pre-existing collection errors.
- `bun run check`: 9190 errors (unchanged).
- `ruff format --check`: clean on all W4-touched files.
- W3 sanitizer test: 8/8 byte-identical PASS.
- `_SKIP_RAG_MAX_BYTES` SoT at `sanitize.py:186`.
- `_SKIP_RAG_PREAMBLE` SoT at `skip_rag.py:42`, middleware re-exports as alias.
- No import cycles; no unexpected importers.

Reusable artefacts from prior waves that W5 MAY use:
- `_validate_admin_dir` in `routers/retrieval.py:842` — helper pattern for admin-path validation (NOT directly applicable to F-11, but consistent style if F-11 adds any config validation).
- Status event pattern from `skip_rag_truncated` (middleware.py) — F-10 should use the SAME event-emission pattern for `token_cascade_failed` and `token_cascade_partial`.
- `SkipRagContext` frozen-dataclass pattern — F-10 and F-11 may benefit from similar structured return types if kind 1 judges it appropriate.

Files UNTOUCHED by W4 that W5 is about to touch:
- `utils/middleware.py` — W5 F-10's primary edit surface (different region from W4 F-9 extraction: cascade logic ~2118, F-9 was at ~3344).
- `routers/retrieval.py` — W5 F-11's primary edit surface (different function from W2 F-4b/F-5 validators: `generate_document_index`, not `update_rag_config`).

No carry from W4. No re-plan.

## Success Criteria

1. **F-10**: `asyncio.gather` Tier-3 loop re-measures total tokens after gather; if over budget, falls back to Tier-2 index-only OR emits `token_cascade_failed` and returns `[]`.
2. **F-10**: Partial failures emit `token_cascade_partial` status event with per-doc success/failure map.
3. **F-10**: `extract_relevant_content_from_document` returns a sentinel (e.g., `None`) on its own exception, NOT the original content.
4. **F-10**: Unit tests cover all-mock-fails, partial-mock-fails, all-success scenarios.
5. **F-10**: Integration: all-success path behaviour unchanged.
6. **F-11**: DB session released BEFORE `run_coroutine_threadsafe` + `future.result(timeout=...)` block.
7. **F-11**: Timeout is dynamic — per-chunk `min(120s, 600s)`, total `len(chunks) * per_chunk`, hard-capped at 1800s.
8. **F-11**: `future.cancel()` called in `finally` block on exception.
9. **F-11**: WARN-level log records timeout configuration + per-chunk wall-clock.
10. **F-11**: Unit tests cover DB-session-closed-before-LLM, future.cancel on timeout, dynamic timeout calc, happy-path regression.
11. Baseline: pytest 405 + N new passed; 9 failed / 5 errors UNCHANGED.
12. `bun run check`: 9190 unchanged (backend-only wave).
13. `ruff format --check` on W5-touched files: exit 0.
14. No forbidden file modified.
15. No out-of-scope finding addressed (except explicit discretionary polish).

## Notes for principal-engineer (kind 1)

- **middleware.py is HUGE** (~5553 LOC). Use `wc -l` first. Targeted Read around line 2118 (Tier-3 loop) and line 2180 (extract_relevant_content_from_document).
- **F-10 sentinel pattern**: change `extract_relevant_content_from_document` to return `None` on its own exception path. Caller (Tier-3 loop) must check `if result is None: record_failure`. This is a behaviour-preserving contract change — the happy path's return is unchanged.
- **F-10 status event**: reuse the existing status-event emission pattern from skip_rag (e.g., `await __event_emitter__({"type": "status", "data": {...}})`). Grep for the exact shape near middleware.py's cascade logic — don't invent.
- **F-11 DB session**: read the current `generate_document_index` flow carefully. The spec says "release BEFORE `run_coroutine_threadsafe`". This likely means: fetch file object, extract chunks list, CLOSE the DB session (explicit `db.close()` or exit the with-block), THEN enter the threadpool/coroutine dispatch. After the LLM call completes, re-fetch the file object by ID and update it.
- **F-11 timeout**: the formula is `total_timeout = min(len(chunks) * per_chunk_timeout, 1800)` where `per_chunk_timeout = min(120, 600)`. For a 10-chunk doc: 10*120=1200s. For a 100-chunk doc: min(12000, 1800) = 1800s. Document the constants clearly.
- **F-11 cancel-on-failure**: wrap the `future.result(timeout=...)` in try/finally. In `finally`, check `if not future.done(): future.cancel()`.
- Discretionary polish: only if T3 cross-validation shows ample budget. Do NOT preemptively fix these in T1 or T2 — F-10 and F-11 are non-deferrable blockers.

## Notes for evaluator (kind 2)

- For F-10: independently construct a test scenario where 3 of 5 `extract_relevant_content_from_document` calls raise exceptions. Verify the cascade correctly identifies partial failure and emits the status event. Don't rely on kind 1's test wiring — write your own.
- For F-11: verify via a DB-session-lifetime tracker (e.g., a mock that records `__enter__` and `__exit__` calls vs the LLM-call entry) that the session closes BEFORE the LLM call. A passing happy-path test that doesn't track session lifetime is insufficient.
- For F-11 timeout: test the formula explicitly with multiple chunk counts (small: 2 chunks, medium: 10 chunks, huge: 100 chunks). Assert the actual timeout passed to `future.result(...)` matches the formula.
- For F-11 cancel-on-failure: verify `future.cancel()` is called on `TimeoutError`, on any `Exception`, AND on normal return (idempotent). Use a mock Future.

Revisit scopes and lenses will be specified by kind 3 in the T0 plan.

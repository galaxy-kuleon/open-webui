# Wave 4 - Reveal Packet

**Wave Objective**: thread per-user credentials through user-triggered LLM calls (F-8 / H-1) + extract skip_rag injection block into a named testable function and rewire tests to import it rather than inline a replica (F-9 / H-5).
**Data-flow segment**: transform (credential propagation) + test-infrastructure
**Blast radius**: medium-cross-module — `_async_llm_completion` is called from multiple utility modules; skip_rag extraction touches `middleware.py` + test files.
**Total waves**: 4 of 5

## Spec Slice (from fix-review-findings-spec.md)

---

### F-8 (H-1) — `_async_llm_completion` runs user-triggered tasks as super-admin

**Severity**: High
**Blast radius**: medium-cross-module (image_analysis, knowledge_export consumers)
**Files in scope**:

- `backend/open_webui/utils/knowledge_export.py` lines ~65-152 (`_async_llm_completion` definition)
- `backend/open_webui/utils/image_analysis.py` lines ~102-121 (`call_vision_llm` consumer)
- `backend/open_webui/utils/knowledge_export.py` organizer path (system-level; may keep super-admin with explicit comment)
- `backend/open_webui/utils/middleware.py` — caller of image_analysis must already have a `user` in scope; thread it through

**Note**: F-1 removed `utils/research.py` in W1, so that caller is gone. Only `image_analysis.py` + knowledge_export callers remain.

**Behavioural requirement**: LLM calls triggered by a specific user's action (image analysis, per-chat knowledge processing) execute with that user's credentials, not a global super-admin.

**Technical constraints**:

1. Change signature: `_async_llm_completion(app, messages, model_id, acting_user, bypass_filter=False)` where `acting_user: UserModel | None`.
2. When `acting_user is None` (system-level tasks only: inbox organizer sweep), log at INFO level `"LLM call under super-admin credential: reason=<caller>"` and continue. This is the ONLY legitimate None case.
3. Update every consumer to pass the real user:
   - `call_vision_llm` — propagate the request's user from its caller (follow the call-chain up and thread it through).
   - `knowledge_export.export_document_files` (if it calls `_async_llm_completion` directly) — pass the uploader.
4. `bypass_filter` stays, but its meaning is "skip filter middleware" not "run as admin". Document this in the function docstring.

**Acceptance criteria**:

- Unit test: invoking `_async_llm_completion` with a non-admin `acting_user` produces an LLM call whose `user` payload reflects that user.
- Unit test: invoking with `acting_user=None` emits the INFO log and proceeds.
- No existing test regresses. Image-analysis integration (if covered) still returns correct results.

**Non-deferrable**: yes.

---

### F-9 (H-5) — `test_skip_rag_injection.py` tests a replica, not real code

**Severity**: High
**Blast radius**: medium (refactor middleware + rewire tests)
**Files in scope**:

- `backend/open_webui/utils/middleware.py` — EXTRACT the skip_rag injection block (~lines 3305-3433 at review time; now shifted due to W3's F-7 edits — re-verify at execution) into a named pure function `build_skip_rag_context(...)` in the same module (or in a new `utils/skip_rag.py` if kind 1 judges it cleaner; prefer staying in middleware for minimum diff).
- `backend/open_webui/test/utils/test_skip_rag_injection.py` — REWRITE to `from open_webui.utils.middleware import build_skip_rag_context` and delete the inline replica.

**Note**: F-1 removes `test_middleware_research.py`'s replica handler issue wholesale (already done in W1). Only the skip_rag replica refactor remains here.

**Behavioural requirement**: tests exercise the shipping code path, not a parallel implementation.

**Technical constraints**:

1. The extracted function signature MUST accept exactly the inputs the real call site passes (form_data, user, files list, request config) — no constructed stand-ins.
2. The extracted function MUST be pure or near-pure: no side effects except logging; no DB writes; filesystem reads are through injected deps if feasible.
3. The original call-site in `middleware.py` becomes a one-liner delegating to the extracted function.
4. Behaviour MUST be byte-identical before and after the extraction. This is a refactor, not a behaviour change. Verify with a pre/post snapshot test.

**Acceptance criteria**:

- `rg 'build_skip_rag_context' backend/` returns: one definition + one call-site in middleware + one import in the test file.
- Test file is reduced in LOC (replica deleted).
- Existing skip_rag scenarios in the test continue to pass.
- New test: `test_skip_rag_respects_server_side_filename_for_extension` — exercises the anti-spoofing protection that the review called out as currently untested (real code uses `file_obj.filename`, not user-supplied name; the test must assert this).

**Non-deferrable**: yes. Without this, future changes can silently regress without tests catching it.

---

## Deferred Items Assigned To This Wave

- From W3 T1: integration test `test_middleware_agent_skill_ids.py::test_chat_flow_path_populates_agent_skill_ids` is a self-documented replica (comprehension copy, not real middleware exercise). **Optional carry**: W4 may refactor this test to exercise real code IF it fits without expanding scope. Priority goes to F-9 primary deliverable first.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/utils/middleware.py` — for F-9 extract (the skip_rag block region touched by W3). NO edits to W3's F-6 `__agent_skill_ids__` lines (3286-3288) unless required for coherent extraction.
  - `backend/open_webui/utils/knowledge_export.py` — F-8 `_async_llm_completion` signature + logging.
  - `backend/open_webui/utils/image_analysis.py` — F-8 consumer update.
  - Any other caller of `_async_llm_completion` discovered via grep — same F-8 pattern.
  - `backend/open_webui/test/utils/test_skip_rag_injection.py` — F-9 rewrite to import from middleware.
  - New test files under `backend/open_webui/test/utils/`:
    - `test_async_llm_completion_user_threading.py` (F-8)
    - `test_skip_rag_extracted_function.py` (F-9 — may be redundant if rewrite of existing test covers all cases)
- **Forbidden files**:
  - Everything under `.agent-team-waves/archive-*/`
  - `backend/open_webui/utils/sanitize.py` (W3 scope — READ-ONLY; `sanitize_llm_injected_markdown` may be called but not modified)
  - `backend/open_webui/routers/retrieval.py` (W2 scope only — READ-ONLY if inspection needed)
  - `backend/open_webui/routers/skills.py` (W2 scope)
  - `backend/open_webui/retrieval/loaders/kg1.py` (W2 scope)
  - `backend/open_webui/utils/tools.py` (W3 scope — READ-ONLY)
  - All `src/` frontend code (no UI changes this wave)
  - All W1/W2/W3 NEW test files (`test_skills_upload.py`, `test_validate_admin_dir.py`, `test_retrieval_config.py`, `test_kg1_drain.py`, `test_sanitize_injected_markdown.py`, `test_middleware_skip_rag_sanitizer.py`, `test_tools_agent_skill_registration.py`, `test_middleware_agent_skill_ids.py`)
- **Out-of-scope items (deferred to future waves)**:
  - F-10, F-11 → Wave 5
  - `RAG_RESEARCH_MODEL` orphan PersistentConfig
  - Pre-existing `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern
  - 13 pre-existing ruff violations in `kg1.py`
  - `add_file_context` at middleware.py:3279 structural coupling observation

## Handoff from Wave 3

W3 closed COMPLETE at commit `5b86aa740` (+1741/-6 LOC, 8 files — single wave commit bundling F-6 + F-7 with documented separability):

- **F-6**: `__agent_skill_ids__` wiring at middleware.py:3286-3288 (lines shifted from 3270-3273 post-W3 edits). Access-control via upstream `accessible_skill_ids` at middleware.py:2999. Consumer at `utils/tools.py:507` now fires.
- **F-7**: `sanitize_llm_injected_markdown(text, file_id)` in `utils/sanitize.py` + hardened middleware.py skip_rag block (sanitize + preamble + 256 KiB UTF-8 byte cap + `skip_rag_truncated` event + double-injection raise). Single-source-of-truth: `_SKIP_RAG_MAX_BYTES = 256 * 1024` at `sanitize.py:186`.

State at W3 close:
- pytest: 382 passed / 9 pre-existing failed / 5 pre-existing collection errors.
- `bun run check`: 9190 errors.
- `ruff format --check`: clean on all W3 files.
- W1 Playwright `admin-rag-settings.spec.ts`: TS-strict exit 0.

**Important**: W3's skip_rag block is NOW larger and more complex (sanitizer + preamble + cap + event + double-injection raise). F-9's "extract to named function" must account for all this W3-added logic. The function `build_skip_rag_context(...)` signature may need to return not just the delimited content but also the preamble-injection signal + the truncation event list.

Reusable artefacts from W3 that W4 MAY use:
- `sanitize_llm_injected_markdown(text, file_id)` at `utils/sanitize.py` — READ-ONLY from W4's POV.
- `_SKIP_RAG_PREAMBLE` constant at `middleware.py:158` — F-9 extraction should keep this constant at module level OR move it with the function into the extracted location.

Files UNTOUCHED by W3 that W4 is about to touch:
- `utils/knowledge_export.py` — W4 F-8's primary edit surface.
- `utils/image_analysis.py` — W4 F-8 consumer update.

No carry from W3 (except the optional replica observation above).

## Success Criteria

1. **F-8**: `_async_llm_completion` signature includes required `acting_user: UserModel | None`; None-case emits INFO log; docstring documents `bypass_filter` semantic separately from user credential.
2. **F-8**: `call_vision_llm` in `image_analysis.py` passes the request's user through (not None, not super-admin).
3. **F-8**: `knowledge_export.export_document_files` passes the uploader's user (if it calls `_async_llm_completion` directly).
4. **F-8**: Any other caller discovered by grep passes the correct user.
5. **F-9**: `build_skip_rag_context(...)` (or equivalent name) exists as a single definition in `utils/middleware.py` (or new `utils/skip_rag.py`).
6. **F-9**: The original call-site in `middleware.py` is a one-liner (or near-one-liner) delegating to the extracted function.
7. **F-9**: `rg 'build_skip_rag_context' backend/` returns exactly: 1 def + 1 call-site + 1 test-import.
8. **F-9**: `test_skip_rag_injection.py` no longer contains a replica; imports the real function.
9. **F-9**: New test `test_skip_rag_respects_server_side_filename_for_extension` exists and passes (exercises anti-spoofing against user-supplied `name`).
10. **Behavioural equivalence**: before/after W4's F-9 extraction, the skip_rag scenarios observable in the existing W3 integration tests (`test_middleware_skip_rag_sanitizer.py`) produce identical outputs — prove with snapshot comparison or re-run the 8 integration tests to confirm PASS.
11. Baseline: pytest 382 + N new passed; 9 failed / 5 errors UNCHANGED.
12. `bun run check`: 9190 unchanged (backend-only wave).
13. `ruff format --check` on W4-touched files: exit 0.
14. No forbidden file modified.
15. No out-of-scope finding addressed.

## Notes for principal-engineer (kind 1)

- **F-8 is cross-module**: grep `_async_llm_completion` first to find ALL callers. Expected: `knowledge_export.py` (organizer + export_document_files), `image_analysis.py` (call_vision_llm). Plus possibly internal callers in middleware.
- For `acting_user=None` case, the INFO log format is: `"LLM call under super-admin credential: reason=<caller>"`. Standardize.
- The `bypass_filter` parameter MUST stay functional (skip filter middleware) but its meaning must be clear in the docstring — NOT a "run as admin" signal.
- **F-9 scope**: the skip_rag block has grown significantly in W3. The extracted `build_skip_rag_context` function will likely need to return a structured result (e.g., `(delimited_content, preamble_needed, truncation_events, ...)`). Design the return type carefully before coding.
- The existing `test_skip_rag_injection.py` may need significant rewrite — it's a replica of pre-W3 logic. Its test cases may need updating to match W3's new semantics (sanitizer + preamble + cap). This is legitimate refactor work, NOT scope expansion.
- Behavioural equivalence: record the OUTPUT of an existing `test_middleware_skip_rag_sanitizer.py` scenario BEFORE F-9 extraction, then verify the same scenario produces identical output AFTER — concrete snapshot.
- W3's F-6 (`__agent_skill_ids__` at middleware.py:3286-3288) is NOT in F-9 extraction scope — don't touch it.

## Notes for evaluator (kind 2)

- For F-8: verify by independently tracing the call graph. Does ANY caller that isn't the system-level organizer use `acting_user=None`? If yes, that's a blocker.
- For F-9: read the extracted `build_skip_rag_context` signature and confirm it takes all inputs needed by the real call-site. Cross-reference the old inline block line-by-line with the new function.
- Run `test_middleware_skip_rag_sanitizer.py` BEFORE and AFTER F-9 is applied (use git stash / workt ree trick or analysis) to confirm behaviour-preserving extraction.
- Don't let F-9's test rewrite sneak in "while I'm here" scope that actually ADDS coverage — every new test assertion must map to an existing behaviour, not a new feature.

Revisit scopes and lenses will be specified by kind 3 in the T0 plan.

# Wave 5 — Reveal Packet

**Wave Objective**: Graft W4 scope (knowledge export + image analysis + acting_user threading + continuation SSE) plus Wave 4 deferred items (image_analysis.py async fix + tools/builtin.run_agent_skill for the 1 carried test).
**Data-flow segment**: transform (knowledge_export LLM bridge; continuation SSE; agent skill dispatch)
**Blast radius**: medium (tools/builtin.py pure append; routers/files.py acting_user graft)
**Total waves**: 5 of 6

## Spec Slice (§W4)

1. `backend/open_webui/tools/builtin.py` — pure append `query_knowledge_bases` block (~230 lines) + `run_agent_skill`. Zero name collision with upstream's 1187-line calendar/automation/task helper expansion.
2. `backend/open_webui/routers/retrieval.py` — append knowledge-export endpoints to the section modified in W4.
3. `backend/open_webui/routers/files.py` — graft F-8 acting_user threading into upload handler.
4. `utils/middleware.py` — verify W4's skip_rag extraction import still resolves (done).
5. `hermes_agent.py` continuation SSE — already in W1.
6. Frontend formatting + 5-line `StatusHistory.svelte` tweak.
7. DO NOT bring `uv.lock` forward — use v0.9.1's verbatim.

## Deferred from Wave 4

- `utils/image_analysis.py` 7 async call sites (Files.update_file_data_by_id) → fix here (W4 wire activates when tests exercise it)
- `test_middleware_skip_rag_sanitizer::test_skill_intercept_bypasses_skip_rag_injection` — needs `tools/builtin.run_agent_skill` symbol; resolves when item 1 lands.

## Constraints

- **Allowed**: `tools/builtin.py`, `routers/files.py`, `routers/retrieval.py` (append only), `utils/image_analysis.py`, `utils/knowledge_export.py` (if additional grafts needed), frontend `StatusHistory.svelte` (5-line tweak)
- **Forbidden**: `main.py`, `middleware.py` (locked), any Wave 1-4 committed content beyond additive extensions
- **Out-of-scope**: W4b deferred UI (separate future run)

## Handoff from Wave 4

Wave 4 closed at `3fcf3083d`. Tests: 135/136 W4 scope; 90/90 W1-3 baseline. npm check 9416.

## Success Criteria

1. `tools/builtin.py` has `query_knowledge_bases` + `run_agent_skill` importable
2. `test_skill_intercept_bypasses_skip_rag_injection` now passes
3. `image_analysis.py` 7 await sites fixed; test_file_upload_image_analysis passes
4. `routers/files.py` acting_user threaded through upload handler; test_async_llm_completion_user_threading passes
5. Knowledge-export tests pass: `test_knowledge_export_llm_bridge.py`, `test_knowledge_export_e2e.py`
6. `test_hermes_continuation.py` passes (already should; verify)
7. Regression: all W1-4 tests still pass
8. npm check ≤ 9416

## Turn Plan

- **T1**: tools/builtin.py query_knowledge_bases + run_agent_skill + image_analysis.py async fix (unblocks deferred test + absorbs W4 carry)
- **T2**: routers/files.py acting_user + knowledge-export endpoints + T1 revisit
- **T3**: cross-validation close

# Wave 4 — Reveal Packet

**Wave Objective**: Graft W3 scope (RAG direct-content + `skip_rag` middleware integration + `/api/v1/hermes/memory` router registration + settings tab) plus Wave 1 deferred builtin_pipes async fix. Hardest wave by collision surface: 3 surgical `middleware.py` grafts, `main.py` route registration with codebase pipe auto-registration, `routers/retrieval.py` admin helpers + 10 admin config fields.
**Data-flow segment**: transform (middleware skip_rag branching) + output (admin REST endpoints + settings UI)
**Blast radius**: large
**Total waves**: 4 of 6

## Spec Slice (from qa-planner memo, §W3)

1. `backend/open_webui/utils/middleware.py` — three surgical grafts:
   - Import: `from open_webui.utils.skip_rag import build_skip_rag_context`
   - `skip_rag` capability read in `process_chat_payload` (anchor: `model_knowledge` variable)
   - Three-way RAG branch at dispatch site (anchor: `chat_completion_files_handler` call site): skip_rag active / file_context_enabled / default RAG
   - Graft the "predict skill intercept" refactor immediately before the skip_rag block
2. `backend/open_webui/routers/retrieval.py` — append module-level helpers `_kg1_allowed_roots`, `_rag_export_allowed_roots`, `_validate_admin_dir` (zero collision). Graft 10 admin-config fields into `update_rag_config` body by section.
3. `backend/open_webui/main.py` — manual route registration:
   - `from open_webui.routers import hermes_memory`
   - `app.include_router(hermes_memory.router, prefix='/api/v1/hermes/memory', tags=['hermes-memory'])` adjacent to existing `memories` router
   - Re-register codebase-built-in pipe auto-registration (`ensure_builtin_pipes`) in v0.9.1's lifespan/startup block
4. `src/lib/components/chat/SettingsModal.svelte` — insert HermesMemory tab entry
5. `src/lib/components/admin/Settings/Documents.svelte` — hand-port 10 new RAG controls (394-line hermes delta vs upstream 19-line touch)
6. `src/lib/components/chat/Chat.svelte` — graft `allSkipRag` capability detection + `process = false` gating at `uploadFile(localStorage.token, file, metadata)` call site
7. `src/lib/components/chat/MessageInput.svelte` — graft `skipRagModels` reactive + upload gate (augmenting Wave 2 work)

## Deferred Items Assigned To This Wave

**From Wave 1 deferred queue**:
- `utils/builtin_pipes.py:41,43,57,69,85,99` — 6 async call sites (Users.get_super_admin_user, Users.get_first_user, Functions.get_function_by_id/update_function_by_id/insert_new_function) unawaited. **MUST fix** — this wave wires `ensure_builtin_pipes` to main.py lifespan; async contract activates.

**From Wave 1 collection errors** (should resolve when symbols land):
- `test_t2_cascade_wiring.py`, `test_t3_edge_cases.py`, `test_token_cascade_tier3_failure.py`, `test_w2t2_phase_a_cascade.py` — all missing `apply_token_budget_cascade` from `utils.middleware`. This symbol must be grafted into middleware.py.
- `test_middleware_skip_rag_sanitizer.py` — missing `_SKIP_RAG_PREAMBLE` from `utils.middleware`.
- `test_document_index_db_session.py`, `test_retrieval_config.py`, `test_validate_admin_dir.py` — missing `generate_document_index` and `_validate_admin_dir` from `utils.retrieval`. Graft into retrieval.py.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/utils/middleware.py` (3 surgical grafts)
  - `backend/open_webui/main.py` (route registration + lifespan hook)
  - `backend/open_webui/routers/retrieval.py` (admin helpers + config fields + generate_document_index)
  - `backend/open_webui/utils/builtin_pipes.py` (deferred async fix — 6 await adds)
  - `src/lib/components/chat/SettingsModal.svelte` (HermesMemory tab)
  - `src/lib/components/admin/Settings/Documents.svelte` (10 new RAG controls)
  - `src/lib/components/chat/Chat.svelte` (allSkipRag gate)
  - `src/lib/components/chat/MessageInput.svelte` (skipRagModels reactive)
- **Forbidden files**:
  - `backend/open_webui/tools/builtin.py` → Wave 5
  - Any Wave 1/2/3 committed content (no retrograde edits)
  - Wave 1 NEW files locked
- **Out-of-scope**:
  - `utils/image_analysis.py` async fix → Wave 5
  - `tools/builtin.py` query_knowledge_bases tool → Wave 5
  - `utils/knowledge_export.py` acting_user threading → Wave 5

## Handoff from Wave 3

Wave 3 closed COMPLETE at `9563a7ba7`. Skill system port + KG1 integration + memory-recall widgets. Tests 45/45 pass; npm check 9391.

## Success Criteria

1. **middleware.py**: `build_skip_rag_context` import present; `skip_rag` capability read near `model_knowledge` anchor; three-way RAG branch at `chat_completion_files_handler` anchor; `apply_token_budget_cascade` function added; `_SKIP_RAG_PREAMBLE` constant added. Zero upstream function deletion.
2. **main.py**: `hermes_memory` router registered at `/api/v1/hermes/memory`; `ensure_builtin_pipes` invoked in lifespan/startup; imports ordered.
3. **retrieval.py**: `_kg1_allowed_roots`, `_rag_export_allowed_roots`, `_validate_admin_dir`, `generate_document_index` helpers present; 10 admin config fields grafted into `update_rag_config`.
4. **builtin_pipes.py**: all 6 await sites fixed; ensure_builtin_pipes callable as async.
5. **Frontend**: SettingsModal has HermesMemory tab; Documents has 10 new controls; Chat.svelte allSkipRag gate wired; MessageInput skipRagModels reactive added.
6. **Test resolution**: 7 previously-collection-error tests now collect and pass (4 cascade + middleware sanitizer + 3 retrieval config tests).
7. **Regression**: pytest Wave 1-3 baseline (45+) still passing.
8. **npm run check** ≤ 9392 (or justified delta).
9. **No forbidden file modified**.
10. **End-to-end**: `curl` hermes_memory endpoint returns 200 (verify route registration worked).

## Turn Plan

- **T1**: middleware.py 3 grafts + builtin_pipes.py async fix + main.py registration
- **T2**: retrieval.py graft + frontend (SettingsModal + Documents + Chat + MessageInput) + T1 revisit (contract-alignment)
- **T3**: close-checklist + global-consistency cross-validation

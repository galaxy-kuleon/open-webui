# Wave Decomposition — Hermes Port v0.8.12 → v0.9.1

Total waves: 6
Mode: auto
Max waves flag: none

Spec memo: `/Users/noelbao/.claude-thx/plans/plan-how-do-we-fancy-quilt.md`
Source branch (replay source): `feat/v0.8.12-hermes-port` stabilised at `27530a551` (local tag `hermes-v0.8.12-final`)
Target branch: `feat/v0.9.1-hermes-port` cut from `v0.9.1` (`0a8a620fb`)

---

## Wave 1: Land collision-free Hermes modules on v0.9.1 (Pre-W0)

- Data-flow segment: entry (staging NEW modules that downstream waves depend on)
- Blast radius: smallest-isolated (no upstream file collision; purely additive)
- Spec sections this wave receives: §Pre-W0, §Prerequisites P4 (async audit), §Strategy (NEW-files clause)
- Planned status: pending
- Deferred items assigned here: None

Wave objective (behavioural): after wave close, `import open_webui.pipes.hermes_agent` and `import open_webui.routers.hermes_memory` succeed; `pytest backend/open_webui/test/hermes/` passes; `package.json` version = `0.9.1-hermes.0`; backend `VERSION` reads `0.9.1-hermes.0`; `npm run check` passes.

## Wave 2: Identity propagation + RAG_USER_COLLECTION_ENABLED + image-analysis gate (W1)

- Data-flow segment: entry (hermes identity headers + admin config + upload gating)
- Blast radius: small (additive env vars; 2 frontend files with modest upstream churn)
- Spec sections this wave receives: §W1
- Planned status: pending
- Deferred items assigned here: None
- Depends on: Wave 1 (hermes_agent pipe must exist for identity tests to run)

Wave objective (behavioural): `pytest backend/open_webui/test/hermes/test_identity.py backend/open_webui/test/pipes/test_hermes_agent_headers.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py` passes; image upload against non-vision model with `ENABLE_IMAGE_ANALYSIS=false` is blocked with the correct user-facing message; `skip_rag` capability toggle appears in the model capabilities admin UI.

## Wave 3: Skill system + KG1 + zip hardening + memory-recall SSE + MemoryChip (W2)

- Data-flow segment: transform (skill upload → scan → install; memory-recall event → UI chip)
- Blast radius: medium (routers/skills.py requires AsyncSession rewrite; retrieval/utils.py has upstream churn)
- Spec sections this wave receives: §W2, §Prerequisites P4 (AsyncSession reference)
- Planned status: pending
- Deferred items assigned here: None
- Depends on: Wave 1 (KG1 loader, opencode, skill_params modules must exist)

Wave objective (behavioural): `pytest backend/open_webui/test/routers/test_skills_upload.py backend/open_webui/test/routers/test_validate_admin_dir.py backend/open_webui/test/pipes/test_hermes_memory_recall.py` passes; `upload_skill_zip` endpoint uses `AsyncSession` DI and rejects zip-slip/symlink/hardlink/absolute-path malformed archives; Playwright `e2e/tests/skill-zip-import.spec.ts` and `hermes-memory-chip.spec.ts` pass.

## Wave 4: RAG direct + skip_rag + /api/v1/hermes/memory + settings tab (W3)

- Data-flow segment: transform (middleware skip_rag branching) + output (admin REST endpoints + settings UI)
- Blast radius: large (middleware.py 3 surgical grafts, main.py route registration, Chat.svelte/MessageInput.svelte/SettingsModal.svelte all touched against upstream refactor)
- Spec sections this wave receives: §W3
- Planned status: pending
- Deferred items assigned here: None
- Depends on: Waves 1, 2 (skip_rag utils, hermes_memory router file, config env vars)

Wave objective (behavioural): `pytest backend/open_webui/test/routers/test_hermes_memory_router.py backend/open_webui/test/routers/test_retrieval_config.py backend/open_webui/test/routers/test_document_index_db_session.py backend/open_webui/test/utils/test_docling_timeout.py` passes; `/api/v1/hermes/memory/*` is routable and IDOR-safe (body-supplied `user_id` ignored); skip_rag three-way branch in middleware fires correctly (skip / file_context / default RAG); Playwright `admin-rag-settings.spec.ts` + `hermes-profile-panel.spec.ts` pass.

## Wave 5: Knowledge export + image analysis + acting_user threading + continuation SSE (W4)

- Data-flow segment: transform (knowledge_export LLM bridge; continuation SSE translator)
- Blast radius: medium (tools/builtin.py pure append; routers/files.py acting_user graft)
- Spec sections this wave receives: §W4
- Planned status: pending
- Deferred items assigned here: None
- Depends on: Waves 1-4 (knowledge_export util, skip_rag extraction, hermes_agent SSE translator already landed)

Wave objective (behavioural): `pytest backend/open_webui/test/utils/test_knowledge_export_llm_bridge.py backend/open_webui/test/utils/test_async_llm_completion_user_threading.py backend/open_webui/test/utils/test_file_upload_image_analysis.py backend/open_webui/test/pipes/test_hermes_continuation.py` passes; `hermes.continuation.suggested` SSE event reaches the frontend via `__event_emitter__`; `query_knowledge_bases` tool is listed and callable via the native function-calling path; Playwright `image-upload.spec.ts` passes.

## Wave 6: ATW archive migration + port retrospective

- Data-flow segment: error-path / bookkeeping (no code; documentation close-out)
- Blast radius: smallest-isolated (docs only)
- Spec sections this wave receives: §ATW archive migration, §Carry-forward
- Planned status: pending
- Deferred items assigned here: None
- Depends on: Waves 1-5 (retrospective summarises what actually happened)

Wave objective (behavioural): `.agent-team-waves/archive-2026-04-23-hermes-port-0.9.1/` contains full copies of prior archives plus `retrospective-0.9.1-port.md` documenting: clean-pull files, hand-grafts per wave, AsyncSession migration deltas, regression hotspots, carry-forward (W5, W4b).

---

## Deferred Queue

- **W5 hardening (F-10 Tier-3 cascade + F-11 total_timeout deadline)** — deferred to separate follow-up PR/run (`feat/v0.9.1-hermes-w5`), NOT in this run. Not blocking.
- **W4b deferred UI** — carried from original 0.8.12 port; not designed in this run. Not blocking.
- **env.py hermes delta remainder** — hermes-only env vars in `backend/open_webui/env.py` NOT referenced by Wave 1 NEW files. Wave 1 grafts the minimum subset (only symbols imported by NEW Wave 1 modules); remainder → assigned to **Wave 2** (alongside `config.py` env var additions in §W1 scope). Not blocking Wave 1 close.
- **sanitize.py hermes delta remainder** — hermes-only helpers in `backend/open_webui/utils/sanitize.py` NOT referenced by Wave 1 NEW files. Wave 1 grafts the minimum subset (only symbols imported by NEW Wave 1 modules); remainder → assigned to **Wave 3 or later** (wave that first imports them). Not blocking Wave 1 close.
- **src/lib/apis/skills/index.ts MODIFIED delta** — assigned to Wave 3 (skill system port). Not blocking Wave 1.
- **async-contract mismatch in `utils/builtin_pipes.py`** — 6 call sites (`:41,43,57,69,85,99`) on `Users.get_super_admin_user`, `Users.get_first_user`, `Functions.get_function_by_id`, `Functions.update_function_by_id`, `Functions.insert_new_function` all unawaited. No Wave 1 runtime caller (`ensure_builtin_pipes` not yet wired to `main.py`). **Target: Wave 4** (the wave that wires pipe auto-registration in `main.py` lifespan). Must activate as pre-existing blocker in Wave 4 reveal packet.
- **async-contract mismatch in `utils/image_analysis.py`** — 7 call sites (`:172,239,256,260,264,273,285`) on `Files.update_file_data_by_id` unawaited. No Wave 1 runtime caller (image analysis not yet invoked). **Target: Wave 5 / W4** (where knowledge-export + image-analysis feature integrates). Must activate as pre-existing blocker in that wave's reveal packet.
- **async-contract mismatch in `utils/skill_params.py`** — 2 call sites (`:144,146`) on `Users.get_super_admin_user`, `Users.get_first_user` unawaited. No Wave 1 runtime caller. **Target: Wave 3** (skill system port wave). Must activate as pre-existing blocker in Wave 3 reveal packet.

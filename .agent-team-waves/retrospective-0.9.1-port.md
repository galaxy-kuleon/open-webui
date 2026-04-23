# Hermes Port v0.8.12 → v0.9.1 — Final Retrospective

**Port run**: 2026-04-23
**Branch**: `feat/v0.9.1-hermes-port` (cut from `v0.9.1` tag `0a8a620fb`)
**Source branch**: `feat/v0.8.12-hermes-port` stabilised at `27530a551` (local tag `hermes-v0.8.12-final`)
**Plan**: `/Users/noelbao/.claude-thx/plans/plan-how-do-we-fancy-quilt.md`
**Strategy**: Hybrid (Option C) — `git checkout` for collision-free NEW files, hand-graft for MODIFIED files with upstream churn, AsyncSession rewrite for DI-migrated endpoints

---

## Wave-by-wave summary

| Wave | Commit | Status | Scope | Turns | Key outcomes |
|------|--------|--------|-------|-------|--------------|
| 1 | `a7a56a5b4` | COMPLETE | 78 NEW collision-free modules + minimum additive env.py/sanitize.py graft + version `0.9.1+hermes.0` | 3 (T1 initial → REDIRECT graft → REDIRECT retry → T2 → T3) | 2 runtime async fixes (Groups.get_groups_by_member_id × 5 sites, Files.get_file_by_id × 1 site); 34 NEW-file TS errors fixed to baseline; PEP 440 version correction. |
| 2 | `8003b02a8` | COMPLETE | W1 scope — hermes env vars + image-analysis gate + skip_rag capability | 3 (T1 → REDIRECT for hydration + pyodide revert → T2 → T3) | Ported +layout.svelte hydration (initial T1 missed it; MessageInput allow-path was dead code). 7 files total including 3 graft-dependency extras. |
| 3 | `9563a7ba7` | COMPLETE | W2 scope — skill system async port + KG1 + memory-recall widgets + Wave 1 skill_params deferred fix | 3 (clean, no retries) | AsyncSession rewrite of `upload_skill_zip`; KG1 engine dispatch in loaders/main.py (scope expansion accepted as graft-dependency precedent); Wave 1 deferred skill_params.py async fix closed. |
| 4 | `3fcf3083d` | COMPLETE_WITH_CARRY | W3 scope — middleware skip_rag + main.py registration + retrieval admin + settings tab + Wave 1 builtin_pipes async fix | 2 (T1 + T2 + inline T3) | 3 surgical middleware grafts + `apply_token_budget_cascade` + `_SKIP_RAG_PREAMBLE` + Phase A file handler; hermes_memory router registered + `ensure_builtin_pipes` lifespan; retrieval 4 admin helpers + 10 config fields. Post-T3 fix for skip_rag.py async + test mocks. 1 test carried (skill-execution block). |
| 5 | `a29c79ace` | COMPLETE_WITH_CARRY | W4 scope — tools/builtin.py `run_agent_skill` + image_analysis async fix + files.py acting_user | 1 compound | W1 deferred image_analysis.py 7 async sites closed via sync-bridge pattern. 1 test still carried — needs middleware skill-execution block (W4-scope gap). |
| 6 | (this commit) | — | ATW archive migration + retrospective | 0 (docs only) | Historical archive consolidation + this retrospective. |

---

## Cumulative deliverables

**Backend** (~15 files touched or added):
- `backend/open_webui/hermes/` (NEW package: `__init__.py`, `identity.py`)
- `backend/open_webui/pipes/hermes_agent.py` (NEW)
- `backend/open_webui/routers/hermes_memory.py` (NEW; httpx-only proxy)
- `backend/open_webui/retrieval/loaders/kg1.py` (NEW)
- `backend/open_webui/utils/skip_rag.py`, `opencode.py`, `knowledge_export.py`, `image_analysis.py`, `skill_params.py`, `docling.py`, `lmstudio_memory.py`, `builtin_pipes.py` (all NEW)
- `backend/open_webui/utils/middleware.py` (3 surgical grafts + cascade helpers)
- `backend/open_webui/utils/sanitize.py` (+177 lines superset — strict additive)
- `backend/open_webui/env.py` (+14 lines additive — HERMES_API_URL, OPENCODE_PATH)
- `backend/open_webui/config.py` (~20 hermes env vars under `# === Hermes additions ===`)
- `backend/open_webui/main.py` (hermes_memory router + ensure_builtin_pipes lifespan)
- `backend/open_webui/routers/skills.py` (AsyncSession DI for upload_skill_zip + pure helpers)
- `backend/open_webui/routers/retrieval.py` (4 admin helpers + 10 config fields + generate_document_index)
- `backend/open_webui/routers/files.py` (F-8 acting_user threading)
- `backend/open_webui/tools/builtin.py` (run_agent_skill + supporting tools)
- `backend/open_webui/models/skills.py` (SkillMeta +4 optional fields)
- `backend/open_webui/retrieval/utils.py` + `retrieval/loaders/main.py` (KG1 integration)

**Frontend** (~11 files):
- `src/lib/apis/hermes/memory.ts` (NEW typed client)
- `src/lib/components/chat/Settings/HermesMemory.svelte` (NEW)
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/AgentSkillStatus.svelte` + `HermesMemoryRecallStatus.svelte` (NEW widgets)
- `src/lib/components/chat/MessageInput.svelte` (image-analysis gate + skipRagModels reactive)
- `src/lib/components/workspace/Models/Capabilities.svelte` (skip_rag toggle)
- `src/lib/stores/index.ts` (imageAnalysisEnabled store)
- `src/routes/+layout.svelte` (hydration via getRAGConfig)
- `src/lib/components/common/FileItem.svelte` (statusText prop)
- `src/lib/apis/files/index.ts` (onProgress param)
- `src/lib/components/chat/Chat.svelte` (allSkipRag gate; terminalServers dead-code removal)
- `src/lib/components/chat/SettingsModal.svelte` (HermesMemory tab)
- `src/lib/components/chat/Messages/ResponseMessage/StatusHistory.svelte` + `StatusItem.svelte` (widget registration)
- `src/lib/components/admin/Settings/Documents.svelte` (10 new RAG controls)

**E2E** (20 Playwright specs + helpers + fixtures — all NEW from hermes-v0.8.12-final)

**Tests** (~40 NEW backend tests across `test/hermes/`, `test/pipes/`, `test/retrieval/`, `test/routers/`, `test/utils/`, `test/apps/webui/`)

**Build config**:
- `package.json` version `0.9.1` → `0.9.1+hermes.0` (PEP 440 local version identifier; `-hermes.0` rejected by `packaging.version.Version`)
- `pyproject.toml` +anyascii==0.3.3 (required by sanitize.py graft)
- `uv.lock` regenerated
- `.gitignore` +.webui_secret_key (on source branch; not ported since v0.9.1 branch inherits upstream gitignore)

---

## Async-migration inventory

v0.9.1 migrated the following APIs from sync to async that bit our port repeatedly:

| API | v0.8.12 | v0.9.1 | Fix site |
|-----|---------|--------|----------|
| `Groups.get_groups_by_member_id` | sync `def` | `async def` | W1 fix — `resolve_hermes_identity` made async; 5 call sites awaited |
| `Files.get_file_by_id` | sync | `async def` | W1 fix in hermes_agent.py:339; W4 fix in skip_rag.py:141 (via injected callable) |
| `Files.update_file_data_by_id` | sync | `async def` | W5 fix — `_update_file_data_sync` sync-bridge helper in image_analysis.py |
| `Users.get_super_admin_user`, `Users.get_first_user` | sync | `async def` | W3 skill_params.py await fix; W4 builtin_pipes.py 6 await fix |
| `Functions.get_function_by_id` / `update_function_by_id` / `insert_new_function` | sync | `async def` | W4 builtin_pipes.py await fix |
| `Skills.get_skill_by_id`, `Skills.insert_new_skill` | sync | `async def` | W3 routers/skills.py AsyncSession rewrite |
| `Chats.get_chat_by_id_and_user_id`, `Chats.get_chat_folder_id`, `Folders.get_folder_by_id_and_user_id` | sync | `async def` | W4 test mock updates (MagicMock → AsyncMock) |
| `get_sorted_filter_ids`, `Functions.get_functions_by_ids` | sync | `async def` | W4 test mock updates |

**Pattern lessons**:
1. Broader async-contract audit at T1 always worth the cost — every wave surfaced at least one missed async call via evaluator's adversarial probe.
2. Injected callables (`get_file_fn` in skip_rag.py) can hide async contract shifts — test those paths end-to-end, not just at the caller.
3. Test-file mocks using `MagicMock` against now-async targets leak coroutines silently; switching to `AsyncMock` is mechanical but must be audited everywhere.

---

## Scope-expansion precedent

Two wave reveals had their file lists expanded during execution:
- **Wave 2**: `src/routes/+layout.svelte` added as 7th file for `imageAnalysisEnabled` hydration (reveal listed 3; 3 dependency-extras accepted by kind 3; +1 hydration file accepted as graft-essential)
- **Wave 3**: `backend/open_webui/retrieval/loaders/main.py` added (reveal listed `retrieval/utils.py` for KG1 kwargs; main.py engine dispatch needed to make those kwargs live)

**Rule established**: additional file modifications are acceptable if they are **dependency fan-out of the primary graft** — i.e., the primary graft is inert without them. Kind 3 must explicitly endorse at retry time.

---

## Regression hotspots

- **`backend/open_webui/utils/middleware.py`**: 593 lines of upstream churn vs our 1160 lines of hermes delta. Required surgical grafts at function/variable anchors (not line numbers). Wave 4's predict-skill-INTERCEPT detection landed but the 113-line skill-EXECUTION block did not — this is the primary open carry.
- **`src/lib/components/chat/Chat.svelte`**: 614 lines of upstream refactor vs our 28 lines of hermes delta. Upstream independently made enough changes that dead-code cleanup in W3 was straightforward; remaining terminalServers removal.
- **`backend/open_webui/tools/builtin.py`**: 1187 lines of upstream calendar/automation expansion vs our 226 lines of hermes additions. Pure append; no collision in practice.
- **`src/lib/components/admin/Settings/Documents.svelte`**: 394 lines of hermes delta vs 19 lines of upstream touch. Hand-ported only the 10 specified W4 controls (NOT the full delta).

---

## Final test state

At Wave 5 close:
- Hermes-scoped + pipes + builtin_pipes + skills_upload + validate_admin_dir + retrieval_config + document_index_db_session + docling_timeout + 4 cascade tests + file_upload_image_analysis + async_llm_completion_user_threading + knowledge_export_llm_bridge + skip_rag_sanitizer: **159 passed / 1 failed**.
- The 1 failure is the W4-gap carry: `test_skill_intercept_bypasses_skip_rag_injection` — needs the 113-line skill-execution middleware block from `hermes-v0.8.12-final:middleware.py:3492-3605`.
- `npm run check`: 9416 errors / 377 files-with-problems. Baseline was 9381 pristine v0.9.1; +35 attributable to our hermes surface, all same-class (i18n store, untyped RAGConfig bindings, null-checks). Zero novel error types.

---

## Carry-forward to post-port work

### Must-do follow-up commit: middleware skill-execution block

**Scope**: graft `backend/open_webui/utils/middleware.py` `if matched_skill:` block (~113 lines) from `hermes-v0.8.12-final:middleware.py:3492-3605` immediately after the existing predict-skill intercept detection. Specifically:
- Copy uploaded files to `work_dir/input/` with sanitize_filename (for skills with work_dir meta)
- Call `skill_params.extract_skill_params` for translation-class skills (currently: `anything-to-docx*` prefix)
- Call `skill_params.build_enriched_skill_prompt`
- Invoke `run_agent_skill` with `__request__`, `__user__`, `__event_emitter__`, `__metadata__`
- Store result in `metadata['__agent_skill_result__']` for `main.py` to detect and bypass LLM

After graft, `test_middleware_skip_rag_sanitizer::test_skill_intercept_bypasses_skip_rag_injection` should pass, bringing the suite to 160/160.

### Deferred per original plan (not this run)

- **Wave 5 hardening** (F-10 Tier-3 cascade + F-11 total_timeout deadline) — separate follow-up PR on top of current port, branch name `feat/v0.9.1-hermes-w5` per plan
- **W4b deferred UI** — carried from original 0.8.12 port; not designed in this run

### Open decisions (to be answered during deployment)

- `__oauth_token__` forwarding in hermes pipe extra_params — currently NOT forwarded (accepted in W1 kind-2 recommendation); revisit if external-provider OAuth delegation needed
- Convergence with upstream v0.9.0's "Memory search" feature — currently keeping `/api/v1/hermes/memory/*` namespace separate
- `USER_PERMISSIONS_FEATURES_HERMES_MEMORY` to participate in upstream's per-feature permissions model — not added this run
- Desktop app (v0.9.0) compatibility with hermes identity propagation — runtime-verify post-merge

---

## Meta: `/atw` protocol observations

**What worked well**:
- Oil-painting overlap (T1 primary → T2 primary+revisit → T3 cross-validation) caught async contract drift multiple times that would have shipped as silent production bugs. Wave 1's adversarial probe pattern became load-bearing.
- `REDIRECT-AND-DESCOPE` with explicit deferred-queue routing kept the blast radius bounded. Without it, Wave 1 would have either escalated or absorbed undeclared scope.
- The `hermes-v0.8.12-final` source tag + `git diff v0.9.1..hermes-v0.8.12-final -- <path>` provided a reliable extraction surface for all grafts.
- Kind 3 (master-advisory) directives stayed principled — explicitly rejected shortcut options (Option A skip-T3 at Wave 1 close) that would have eroded protocol discipline.

**What would be better next time**:
- **Wave 4's reveal packet missed the skill-execution block**. The spec memo at `/Users/noelbao/.claude-thx/plans/plan-how-do-we-fancy-quilt.md` §W3 specified "3 surgical grafts" in middleware.py. The predict-skill-intercept refactor was named but the downstream skill-EXECUTION block (which consumes the match and invokes the skill) was not. A more thorough pre-wave reveal scan of the hermes middleware diff would have caught this.
- **Version string decision timing**: The plan locked `0.9.1-hermes.0` as user selection but PEP 440 rejects that syntax. The Wave 1 principal made the PEP 440 correction (`+hermes.0`) during execution. Better: have the plan author verify PEP 440 conformance before locking the user's chosen version.
- **Test count reporting drift**: Several waves had minor "N/N" reporting mismatches between principal and evaluator. Adopting a single canonical count command at kind-3 T0 plan time would remove ambiguity.

**Budget summary**: 5 waves committed (+ Wave 6 docs close). Average 2-3 turns per wave, with 1 retry in Wave 1 (identity async) and 1 retry in Wave 2 (layout hydration + pyodide). Zero escalations to user decision. All deferrals correctly routed into later waves.

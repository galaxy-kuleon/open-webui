# Wave 2 — Reveal Packet

**Wave Objective**: Graft W1 scope (identity propagation support vars + RAG_USER_COLLECTION_ENABLED + image-analysis capability gate) onto the v0.9.1 surface. Add env vars to `config.py`, conditional image-upload gating to `MessageInput.svelte`, and `skip_rag` capability toggle to `Models/Capabilities.svelte`. Zero upstream structural changes beyond additive grafts at named anchors.
**Data-flow segment**: entry (hermes identity headers + admin config + upload gating)
**Blast radius**: small (additive env vars; 2 frontend files with modest upstream churn)
**Total waves**: 2 of 6

## Spec Slice (from qa-planner memo)

From `/Users/noelbao/.claude-thx/plans/plan-how-do-we-fancy-quilt.md` §W1 — Identity propagation, RAG_USER_COLLECTION_ENABLED, image-analysis gate:

> **Actions:**
> 1. `backend/open_webui/config.py` — append the ~20 hermes env vars (name-verified zero-collision with upstream's new STORAGE_LOCAL_CACHE, ENABLE_PASSWORD_CHANGE_FORM, USER_PERMISSIONS_FEATURES_AUTOMATIONS, ENABLE_CALENDAR, ENABLE_AUTOMATIONS, AUTOMATION_*, RAG_RERANKING_BATCH_SIZE, AUDIO_TTS_MISTRAL_*). Use anchor-based insertion (below the `RAG_` block).
> 2. `src/lib/components/chat/MessageInput.svelte` — re-find the `uploadFile` call site and `let paste = async (e)` handler. Graft the `imageAnalysisEnabled` gate + vision-capability check.
> 3. `src/lib/components/workspace/Models/Capabilities.svelte` — add the `skip_rag` capability toggle.
> 4. Skip the `research.py` removal (upstream already removed it).
>
> **Tests:**
> - `pytest backend/open_webui/test/hermes/test_identity.py backend/open_webui/test/pipes/test_hermes_agent_headers.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py -x`
> - `npm run check`

## Source of truth for grafted content

- **`config.py`**: diff `git diff v0.9.1..hermes-v0.8.12-final -- backend/open_webui/config.py` shows the hermes additions. Extract ONLY the hermes-specific additions (RAG_USER_COLLECTION_ENABLED, HERMES_*, SKIP_RAG_*, ENABLE_IMAGE_ANALYSIS, IMAGE_ANALYSIS_*, etc.) and append at the correct anchor (below RAG block).
- **`MessageInput.svelte`**: diff `git diff v0.9.1..hermes-v0.8.12-final -- src/lib/components/chat/MessageInput.svelte` shows 74 insertions / 0 deletions hermes-only. The changes cluster around (a) the `paste = async (e)` handler (image clipboard handling) and (b) the `uploadFile(...)` call (image analysis gating).
- **`Capabilities.svelte`**: diff `git diff v0.9.1..hermes-v0.8.12-final -- src/lib/components/workspace/Models/Capabilities.svelte` shows 13 insertions. The `skip_rag` toggle.

## Deferred Items Assigned To This Wave

**None from Wave 1's deferred queue** — skill_params.py async (Wave 3), builtin_pipes.py async (Wave 4), image_analysis.py async (Wave 5) are all assigned elsewhere.

However, Wave 2's work on MessageInput.svelte must NOT touch image_analysis.py runtime behaviour — the gating logic reads a capability flag only; it does NOT invoke `Files.update_file_data_by_id` (the source of the async mismatch). Confirm during audit.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/config.py` (hermes-only additive graft; anchor = below RAG_ block)
  - `src/lib/components/chat/MessageInput.svelte` (2 anchor-based grafts: paste handler + uploadFile gate)
  - `src/lib/components/workspace/Models/Capabilities.svelte` (skip_rag toggle insertion)
- **Forbidden files** (deferred to later waves):
  - `backend/open_webui/utils/middleware.py` → Wave 4
  - `backend/open_webui/main.py` → Wave 4
  - `backend/open_webui/routers/retrieval.py` → Wave 4/5
  - `backend/open_webui/routers/skills.py` → Wave 3
  - `backend/open_webui/tools/builtin.py` → Wave 5
  - `src/lib/components/chat/Chat.svelte` → Wave 4
  - `src/lib/components/chat/SettingsModal.svelte` → Wave 4
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusHistory.svelte` → Wave 3
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte` → Wave 3
  - `backend/open_webui/utils/image_analysis.py` (async mismatch fix) → Wave 5
  - Any Wave 1 NEW file that is runtime-correct (no re-editing of W1 files)
- **Out-of-scope items**:
  - `research.py` removal — upstream already removed it, skip
  - Frontend skill-related UI wiring (Wave 3)
  - Middleware skip_rag integration (Wave 4)

## Handoff from Wave 1

Wave 1 closed COMPLETE at `a7a56a5b4`. Delivered: 78 NEW collision-free Hermes modules + minimum additive graft on env.py/sanitize.py/pyproject.toml/uv.lock + version `0.9.1+hermes.0`. Two runtime async-contract fixes (Groups.get_groups_by_member_id × 5 sites; Files.get_file_by_id × 1 site) + secondary UPLOAD_DIR import source correction.

Wave 1 staged state now on `feat/v0.9.1-hermes-port`:
- `package.json` = `0.9.1+hermes.0`
- `env.py` +14 lines (HERMES_API_URL, OPENCODE_PATH, etc.)
- `sanitize.py` +177 lines (hermes-branch superset prepended)
- `pyproject.toml` +anyascii==0.3.3
- All hermes runtime-reachable code is async-contract-correct

Test baseline from Wave 1:
- `pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/ backend/open_webui/test/utils/test_builtin_pipes.py -q`: 23/23 pass
- `npm run check`: 9381 errors / 377 files-with-problems (= pristine v0.9.1 baseline)

Any delta in these numbers post-Wave-2 must be either zero (additive grafts shouldn't break existing tests or types) or explicitly justified.

## Success Criteria

1. **`config.py` anchored graft** — hermes env vars appended at a logical anchor below the existing RAG block. Zero upstream-line modification. `git diff v0.9.1 -- backend/open_webui/config.py | grep "^-" | grep -v "^---"` empty.
2. **`MessageInput.svelte` graft** — paste handler + uploadFile gate inserted at function/reactive-statement anchors. Zero upstream function deletions. `git diff v0.9.1 -- src/lib/components/chat/MessageInput.svelte | grep "^-" | grep -v "^---"` only contains upstream lines the graft contextually touched (e.g., changed a single upstream line to accommodate the gate condition — justify each).
3. **`Capabilities.svelte` graft** — skip_rag capability toggle row inserted. Zero upstream-line modification.
4. **Env var cross-check** — names like `RAG_USER_COLLECTION_ENABLED`, `HERMES_*`, `ENABLE_IMAGE_ANALYSIS` do not collide with upstream v0.9.1 config.py names (`STORAGE_LOCAL_CACHE`, `ENABLE_PASSWORD_CHANGE_FORM`, `USER_PERMISSIONS_FEATURES_AUTOMATIONS`, `ENABLE_CALENDAR`, `ENABLE_AUTOMATIONS`, `AUTOMATION_*`, `RAG_RERANKING_BATCH_SIZE`, `AUDIO_TTS_MISTRAL_*`).
5. **`pytest backend/open_webui/test/hermes/test_identity.py backend/open_webui/test/pipes/test_hermes_agent_headers.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py -x`** — all pass (must not regress from Wave 1's 23/23 baseline).
6. **`npm run check`** — error count ≤ 9381 (Wave 1 baseline). Any new NEW-file-attributable errors = FAIL.
7. **No forbidden file modification** — `git diff HEAD~1 --name-only` since Wave 1 commit lists ONLY the 3 Wave 2 files.
8. **No regression in Wave 1 tests** — `pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/ backend/open_webui/test/utils/test_builtin_pipes.py -q` still 23/23.
9. **Graft minimality** — each hermes env var added is either (a) imported by a Wave 1 NEW file (backward reference) OR (b) explicitly named in §W1 scope (RAG_USER_COLLECTION_ENABLED etc.). No forward-reference speculative additions.
10. **Git status clean post-commit**.

## Turn Plan (kind 3 will refine at T0)

Suggested 3-turn shape:

- **T1 — Primary (config.py graft + MessageInput.svelte graft + Capabilities.svelte graft)**
  - Inspect `git diff v0.9.1..hermes-v0.8.12-final -- backend/open_webui/config.py` to identify the hermes-only additions
  - Apply additions to config.py at the below-RAG anchor; run config import smoke
  - Apply MessageInput.svelte grafts at the `paste` / `uploadFile` anchors using function-name matching
  - Apply Capabilities.svelte graft
  - Revisit: none (first turn)
- **T2 — Test + typecheck + Wave 1 regression + T1 contract-alignment revisit**
  - Run pytest hermes + `npm run check`
  - Revisit T1: verify graft-minimality (each added env var imported by some NEW file or named in scope)
  - Verify no upstream function deletion in Svelte files
- **T3 — Cross-validation (global-consistency)**
  - Full verification battery
  - Verify Wave 1 tests still 23/23
  - Close checklist + retrospective annotations

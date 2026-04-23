# Wave 3 — Reveal Packet

**Wave Objective**: Graft W2 scope (skill system port + KG1 integration + zip-upload hardening + memory-recall SSE + MemoryChip wiring) onto v0.9.1 with async-DI migration for the critical `upload_skill_zip` endpoint, fix the W1-deferred skill_params.py async mismatch, register hermes status widgets in StatusHistory.
**Data-flow segment**: transform (skill upload → scan → install; memory-recall event → UI chip)
**Blast radius**: medium (routers/skills.py requires AsyncSession rewrite; retrieval/utils.py has upstream churn; frontend StatusHistory graft)
**Total waves**: 3 of 6

## Spec Slice (from qa-planner memo, §W2)

> 1. **`backend/open_webui/routers/skills.py` — rewrite `upload_skill_zip` for AsyncSession.** Pure helper functions `_is_unsafe_zip_member`, `_assert_no_symlinks_in_tree` port unchanged. Replace `db: Session = Depends(get_session)` with `db: AsyncSession = Depends(get_async_session)`; replace `db.query(...)` calls with `await db.execute(select(...))` patterns. Reference: v0.9.1 `create_new_skill` and `update_skill_by_id` endpoints show the exact migration idiom.
> 2. `backend/open_webui/models/skills.py` — merge `SkillMeta` additions additively.
> 3. `backend/open_webui/retrieval/utils.py` — graft 55-line KG1 integration near new `build_loader_from_config` + `filter_accessible_collections`.
> 4. `src/lib/components/chat/Chat.svelte` — remove dead `terminalServers` restore block (if still present upstream); add skill-import UI only if upstream didn't.
> 5. `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusHistory.svelte` + `StatusItem.svelte` — graft 7 lines to register `HermesMemoryRecallStatus` and `AgentSkillStatus`.
> 6. `hermes_agent.py` memory-recall SSE translator — already landed via Wave 1.

## Deferred Items Assigned To This Wave

**From Wave 1 deferred queue:**

- **`utils/skill_params.py:144,146`** — `Users.get_super_admin_user`, `Users.get_first_user` unawaited. Fix: add `await` at both call sites; confirm enclosing function is `async def` or propagate async up the call chain. Activate as pre-existing blocker.
- **`test_skills_upload.py` collection error** — missing `_assert_no_symlinks_in_tree` from `routers.skills`. Resolves when skill system port lands (item 1 of this wave).

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/routers/skills.py` (AsyncSession rewrite + helper function addition)
  - `backend/open_webui/models/skills.py` (SkillMeta additive merge)
  - `backend/open_webui/retrieval/utils.py` (KG1 integration graft)
  - `backend/open_webui/utils/skill_params.py` (Wave 1 deferred async fix)
  - `src/lib/components/chat/Chat.svelte` (dead-code removal / skill import UI)
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusHistory.svelte` (register 2 new status types)
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte` (register 2 new status types)
  - `src/lib/apis/skills/index.ts` (if upstream is MODIFIED and our hermes branch has additions)
- **Forbidden files**:
  - `backend/open_webui/utils/middleware.py` → Wave 4
  - `backend/open_webui/main.py` → Wave 4
  - `backend/open_webui/routers/retrieval.py` → Wave 4
  - `backend/open_webui/tools/builtin.py` → Wave 5
  - `src/lib/components/chat/MessageInput.svelte` (Wave 2 accepted; no re-edit)
  - `src/lib/components/chat/SettingsModal.svelte` → Wave 4
  - Any Wave 1 NEW file (locked)
- **Out-of-scope**:
  - `/api/v1/hermes/memory/*` router registration in main.py → Wave 4
  - skip_rag middleware integration → Wave 4
  - HermesMemory settings tab → Wave 4

## Handoff from Wave 2

Wave 2 closed COMPLETE at `8003b02a8`. Deliverables: hermes env vars in `config.py`, image-analysis gate + hydration in frontend, skip_rag capability toggle. 7 files + 1 revert. Pytest 23/23; npm check 9392.

**Async-migration reference files** (read these before rewriting `upload_skill_zip`):

- `backend/open_webui/routers/skills.py` (v0.9.1 current state — contains 5 async endpoints with `AsyncSession = Depends(get_async_session)` pattern, including `create_new_skill` and `update_skill_by_id`)
- `backend/open_webui/models/skills.py` (v0.9.1 — `Skills.get_skills(db)` is async; confirm the exact method signatures)
- `backend/open_webui/internal/db.py` (`get_async_session` definition)

Source of grafted content: `hermes-v0.8.12-final` tag.

## Success Criteria

1. **`upload_skill_zip` uses AsyncSession DI** — signature is `async def upload_skill_zip(request, file, user=Depends(get_verified_user), db: AsyncSession = Depends(get_async_session))`. Zero `db: Session` remaining.
2. **All `db.query(...)` calls in skill-upload path replaced with `await db.execute(select(...))`**.
3. **Pure helpers `_is_unsafe_zip_member` and `_assert_no_symlinks_in_tree` present** in skills.py; unit tests for them pass.
4. **`pytest backend/open_webui/test/routers/test_skills_upload.py -x`** — collection error resolved; tests pass.
5. **`pytest backend/open_webui/test/routers/test_validate_admin_dir.py -x`** — this tests `_validate_admin_dir` which is in retrieval.py (Wave 4 target); expected to still be in collection-error state → acceptable deferral, confirm target wave.
6. **`skill_params.py` async mismatch fixed** — `Users.get_super_admin_user` and `Users.get_first_user` at `:144,146` awaited; any function that calls skill_params helpers must be `async def`.
7. **StatusHistory + StatusItem register AgentSkillStatus + HermesMemoryRecallStatus** — 7-line grafts; visual status widgets will now render when `hermes.memory.recalled` or `hermes.agent.skill` SSE events arrive.
8. **`pytest backend/open_webui/test/pipes/test_hermes_memory_recall.py`** — pass (hermes_agent.py SSE translator already landed in Wave 1; this test exercises the translator).
9. **Chat.svelte**: verify `terminalServers` restore block handling; skill-import UI only added if v0.9.1 doesn't already provide it.
10. **No regression** — Wave 1 + Wave 2 test baselines unchanged (24/24 = 23 + 1 new skill_params test; npm check ≤ 9392).
11. **No forbidden file modified** — `git diff HEAD~1 --name-only` lists only Wave 3 allowed files.
12. **Retrospective annotations** — async-migration inventory: for every `db.query`/`db.add`/`db.commit` call in skills.py, show before/after.

## Turn Plan (kind 3 will refine at T0)

- **T1 Primary**: routers/skills.py async rewrite (the hardest single change); models/skills.py additive merge; skill_params.py async fix.
- **T2 Primary + Revisit**: retrieval/utils.py KG1 graft; StatusHistory + StatusItem grafts; Chat.svelte dead-code check. Revisit T1 with contract-alignment lens (AsyncSession pattern consistency, await coverage).
- **T3 Cross-validation**: full verification battery + close checklist + retrospective.

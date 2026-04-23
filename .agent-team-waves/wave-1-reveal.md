# Wave 1 — Reveal Packet

**Wave Objective**: Land all collision-free NEW Hermes modules on `v0.9.1` by checking them out from `hermes-v0.8.12-final`, bump `package.json` to `0.9.1-hermes.0`, verify imports resolve and targeted pytest/typecheck pass.
**Data-flow segment**: entry (staging NEW modules that downstream waves depend on)
**Blast radius**: smallest-isolated (purely additive; no upstream file collision)
**Total waves**: 1 of 6

## Spec Slice (from qa-planner memo)

From `/Users/noelbao/.claude-thx/plans/plan-how-do-we-fancy-quilt.md` §Pre-W0:

> Files to pull via `git checkout hermes-v0.8.12-final -- <path>` (zero upstream collision; verified):
>
> **Backend:**
>
> - `backend/open_webui/hermes/__init__.py`
> - `backend/open_webui/hermes/identity.py`
> - `backend/open_webui/pipes/hermes_agent.py`
> - `backend/open_webui/routers/hermes_memory.py`
> - `backend/open_webui/retrieval/loaders/kg1.py`
> - `backend/open_webui/utils/skip_rag.py`
> - `backend/open_webui/utils/opencode.py`
> - `backend/open_webui/utils/knowledge_export.py`
> - `backend/open_webui/utils/image_analysis.py`
> - `backend/open_webui/utils/skill_params.py`
> - `backend/open_webui/utils/docling.py`
> - `backend/open_webui/utils/lmstudio_memory.py`
> - All NEW tests under `backend/open_webui/test/hermes/`, `test/pipes/`, `test/utils/`
>
> **Frontend:**
>
> - `src/lib/apis/hermes/memory.ts`
> - `src/lib/apis/skills/index.ts` (additions)
> - `src/lib/components/chat/Settings/HermesMemory.svelte`
> - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/AgentSkillStatus.svelte`
> - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/HermesMemoryRecallStatus.svelte`
> - All `e2e/tests/*.spec.ts` files that are NEW in the source branch
>
> **Version bump:** Edit `package.json` → `"version": "0.9.1-hermes.0"`.

From §Prerequisites P4 (async-contract audit — read-only reference):

> Read these v0.9.1 files end-to-end before writing any code:
>
> - `backend/open_webui/functions.py` — confirm pipe `extra_params` (v0.9.1 adds `__oauth_token__`; decide if hermes pipe should forward it).
> - `backend/open_webui/utils/plugin.py` — `load_function_module_by_id` is async; caller-internal so our pipe definition stays.
> - `backend/open_webui/routers/memories.py` — canonical async router with `ASYNC_VECTOR_DB_CLIENT` + `AsyncSession` DI.
> - `backend/open_webui/routers/skills.py` — canonical `AsyncSession = Depends(get_async_session)` + `await db.execute(...)` pattern.
>
> **Key confirmed facts (already verified):**
>
> - Our `async def pipe(self, body, __event_emitter__, __user__, __chat_id__, __files__, __metadata__, **kwargs)` signature still works.
> - `upload_skill_zip` (W2) MUST be rewritten for `AsyncSession`.
> - `hermes_memory` router (W3) is httpx-only — no DB → no async-session migration needed.

From §Strategy (Option C hybrid):

> **NEW files that upstream never touches** → restore from `ba2feabc5` via `git checkout ba2feabc5 -- <path>`.

(Note: the earlier ba2feabc5 was superseded by `hermes-v0.8.12-final` = `27530a551`; use the tag.)

## Deferred Items Assigned To This Wave

None.

## Constraints for This Wave

- **Allowed files to modify**:
  - Any NEW file listed above (i.e. files that do not exist on `v0.9.1` but exist on `hermes-v0.8.12-final`).
  - `package.json` (version field only).
- **Forbidden files**:
  - Any file that exists on `v0.9.1` and would be overwritten by `git checkout` — MUST verify non-existence before checkout.
  - `backend/open_webui/utils/middleware.py` (defer to Wave 4)
  - `backend/open_webui/main.py` (defer to Wave 4)
  - `backend/open_webui/routers/retrieval.py` (defer to Wave 4)
  - `backend/open_webui/routers/skills.py` (defer to Wave 3)
  - `backend/open_webui/tools/builtin.py` (defer to Wave 5)
  - `backend/open_webui/config.py` (defer to Wave 2)
  - `src/lib/components/chat/Chat.svelte` (defer)
  - `src/lib/components/chat/MessageInput.svelte` (defer)
  - `src/lib/components/chat/SettingsModal.svelte` (defer)
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusHistory.svelte` (defer to Wave 3)
  - `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte` (defer to Wave 3)
  - `uv.lock`, `package-lock.json` (DO NOT bring forward; use upstream's verbatim)
- **Out-of-scope items (deferred to future waves)**:
  - Any `backend/open_webui/utils/middleware.py` skip_rag integration → Wave 4
  - Any `backend/open_webui/main.py` route registration → Wave 4
  - `backend/open_webui/config.py` env var additions → Wave 2
  - Any skill router changes → Wave 3
  - Any ATW archive migration → Wave 6

## Handoff from Wave N-1

This is the first wave. No prior wave context.

**Pre-wave state (verified by kind 7 before this reveal):**

- Branch `feat/v0.9.1-hermes-port` is checked out, at commit `0a8a620fb` (upstream `v0.9.1`).
- Working tree clean.
- Source branch `feat/v0.8.12-hermes-port` stabilised at `27530a551`, tagged `hermes-v0.8.12-final` locally.
- `package.json` currently reads `0.9.1`.
- `.agent-team-waves/decomposition.md` written; active marker `.wave-turn-active` present.

## Success Criteria

Each criterion must be independently verifiable by the evaluator:

1. **All NEW backend module files present on the branch** — `ls` of each path listed in §Spec Slice returns success; each file matches the `hermes-v0.8.12-final` version byte-for-byte (`git diff hermes-v0.8.12-final -- <path>` empty for each).
2. **All NEW frontend files present on the branch** — same verification as above for frontend paths.
3. **All NEW test files present and discovered by pytest** — `pytest --collect-only backend/open_webui/test/hermes/` and `pytest --collect-only backend/open_webui/test/pipes/` list the new test modules; no collection errors.
4. **Zero upstream files modified** — `git diff v0.9.1 --name-only` shows ONLY newly added files + `package.json`; no MODIFIED status on any upstream file.
5. **Python import smoke passes** — `cd backend && python -c "from open_webui.hermes.identity import resolve_hermes_identity; from open_webui.pipes.hermes_agent import Pipe; from open_webui.routers.hermes_memory import router; from open_webui.utils.skip_rag import build_skip_rag_context; print('ok')"` prints `ok` without error.
6. **Hermes-scoped pytest passes** — `cd backend && pytest open_webui/test/hermes/ open_webui/test/utils/test_builtin_pipes.py -x -q` exits 0 (or documented-to-fail tests are listed explicitly in the wave retrospective as "blocked on later wave").
7. **Frontend typecheck passes** — `npm run check` exits 0.
8. **Version bumped** — `jq -r .version package.json` outputs `0.9.1-hermes.0`; backend runtime version resolves to same: `python3 -c "import json; print(json.load(open('package.json'))['version'])"` → `0.9.1-hermes.0`.
9. **Async-contract audit documented** — a brief note (in the wave retrospective) confirming which v0.9.1 reference files were read and whether any NEW-file signatures need adjustment (e.g., does `hermes_agent.py` pipe need to forward the new `__oauth_token__` extra param? Answer yes/no with justification).
10. **Git status clean post-commit** — after the final commit, `git status --short` is empty.

## Turn Plan (kind 3 will refine at T0)

Suggested 3-turn shape:

- **T1 — Primary (discovery + checkout + version bump)**
  - Enumerate the exact set of NEW files on `hermes-v0.8.12-final` that are absent from `v0.9.1` (use `git diff v0.9.1..hermes-v0.8.12-final --diff-filter=A --name-only` for additions).
  - Cross-check against the spec's NEW-file list; flag discrepancies.
  - `git checkout hermes-v0.8.12-final -- <path>` for each verified NEW file.
  - Bump `package.json` version.
  - Run import smoke; report.
  - Revisit: none (first turn).
- **T2 — Test execution + revisit(T1, contract-alignment)**
  - Run targeted pytest + `npm run check`.
  - Revisit T1 with `contract-alignment` lens: check that the NEW files' imports resolve against v0.9.1's stdlib-and-dep surface (e.g. did any of our NEW modules import from a v0.8.12-only private API that moved?).
  - Fix any import or signature mismatches surfaced.
- **T3 — Cross-validation (global-consistency)**
  - Full `npm run check`; run full hermes-scoped pytest collection.
  - Verify async-contract audit: read v0.9.1 `functions.py` extra_params; confirm NEW `hermes_agent.py` pipe signature compatible; note `__oauth_token__` decision.
  - Verify `git diff v0.9.1` shows only NEW files + package.json.
  - Revisit T1, T2 with `global-consistency` lens.

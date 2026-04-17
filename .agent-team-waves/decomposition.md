# Wave Decomposition — Hermes Port Follow-up

Total waves: 2
Mode: auto
Max waves flag: none
Source spec: `/Users/noelbao/.claude-kg/plans/dynamic-snacking-sprout.md`
Base branch: `feat/v0.8.12-hermes-port`
Prior /atw run archived at `./archive-2026-04-17-hermes-port-main/`

## Wave 1: Pipe cleanup + Connection-vs-pipe docstring clarification

- Data-flow segment: error-path (dead code + docs + regression test)
- Blast radius: smallest-isolated (single pipe file + its tests)
- Spec sections this wave receives:
  - "Wave 1 — Pipe cleanup + docstring clarification" block (spec lines 19-33)
  - Context gap #1 and #2 (spec lines 9-11)
  - Forbidden list, Wave-1-specific constraints (spec lines 62-69)
  - Verification gate for Wave 1 (spec line 86)
- Planned status: pending
- Deferred items assigned here: None

### Wave 1 Turns

| Turn | Primary Scope | Revisit | Lens |
|------|---------------|---------|------|
| T1 | Delete `reasoning_content` branch at `backend/open_webui/pipes/hermes_agent.py:212-220` + add regression test to `backend/open_webui/test/utils/test_hermes_pipe.py` asserting a chunk containing `reasoning_content` is yielded as `content` without triggering `thinking` status | — | — |
| T2 | Add Connection-vs-pipe clarification to `pipe()` docstring at line 100: enumerate the 4 load-bearing pipe features (tool progress SSE translation, file-path injection, manifold prefix stripping, session header gate) | T1 | contract-alignment |
| T3 | Full cross-validation: 18 tests pass, docstring coherent, no orphaned imports, no other call sites of deleted branch | T1, T2 | global-consistency |

## Wave 2: Playwright smoke tests for Wave 1-3 UI features

- Data-flow segment: output (end-to-end behavioural verification)
- Blast radius: moderate (3 new spec files + helper + fixture)
- Spec sections this wave receives:
  - "Wave 2 — Playwright smoke tests" block (spec lines 35-50)
  - Critical reuse points (spec lines 53-59)
  - Forbidden list, Wave-2-specific constraints (spec lines 62-70)
  - Risks #1-#4 (spec lines 73-80)
  - Verification gate for Wave 2 (spec line 88)
- Planned status: pending
- Deferred items assigned here: None

### Wave 2 Turns

| Turn | Primary Scope | Revisit | Lens |
|------|---------------|---------|------|
| T1 | `e2e/tests/image-upload.spec.ts` four quadrants: (vision × enabled/disabled) × (non-vision × enabled/disabled). Plus helper `e2e/helpers/admin.ts` (admin login + RAG config toggle, env-var overrides for ADMIN_EMAIL / ADMIN_PASSWORD). Uses existing `auth.ts` + `chat.ts` helpers. | — | — |
| T2 | `e2e/tests/skill-zip-import.spec.ts` with fixture `e2e/fixtures/test-skill.zip` (valid `SKILL.md`) + negative test (invalid ZIP missing `SKILL.md`). ZIP fixture must conform to `routers/skills.py:289-295` validator. | T1 | contract-alignment |
| T3 | `e2e/tests/admin-rag-settings.spec.ts`: set all 10 new RAG controls to non-default values via UI, save, reload, assert preserved. Final gate: `bunx playwright test --config=e2e/playwright.config.ts --list` shows 3 new specs collected without parse errors. | T1, T2 | global-consistency |

## Deferred Queue

- None

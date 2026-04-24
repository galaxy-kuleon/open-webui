# Wave 1 - Reveal Packet

**Wave Objective**: delete dead `utils/research.py` + its test replicas, and add the missing `RAG_USER_COLLECTION_ENABLED` Switch to the admin RAG settings UI.
**Data-flow segment**: output (UI gap) + isolated-removal (dead code)
**Blast radius**: smallest-isolated
**Total waves**: 1 of 5

## Spec Slice (from fix-review-findings-spec.md)

---

### F-1 (H-4) — Dead code: `utils/research.py` has zero production callers

**Severity**: High
**Blast radius**: smallest-isolated (pure deletion)
**Files in scope**:

- `backend/open_webui/utils/research.py` (DELETE)
- `backend/open_webui/test/utils/test_middleware_research.py` (DELETE)
- `backend/open_webui/test/utils/test_research_command.py` (DELETE if exists)
- `backend/open_webui/test/utils/test_research_*` (grep-find all, DELETE)
- Any caller that imports from `utils.research` (search + remove)
- `.agent-team-waves/archive-2026-04-17-hermes-port-main/` references: do NOT touch (historical record)

**Observation from review**: `run_research` is imported only from test files that duplicate the intended dispatcher inline — no `middleware.py`, `main.py`, or router references it. 536 LOC of production code plus ~1300 LOC of replica-based tests with zero real coverage.

**Why delete vs wire**: wiring it requires building a `/research` command dispatcher the review identified as missing. That is new feature work, not a review fix. Deletion is the correctness-preserving move.

**Behavioural requirement**: after this finding is closed, `rg 'from open_webui.utils.research|from open_webui\.utils\.research|import .*research' backend/ src/` returns no matches in non-archive paths.

**Acceptance criteria**:

- `rg -l 'utils\.research|utils/research' backend/ src/` returns no files (archive excluded).
- `uv run python -c "import open_webui.utils"` succeeds.
- `uv run pytest backend/open_webui/test/` exit code 0 (deleted tests must not reappear as collection errors).
- The deletion is a single logical commit-unit inside the wave (not spread across waves).

**Non-deferrable**: yes. Leaving shipped-but-dead code in the tree is a future-maintenance trap and will confuse downstream findings (F-5 test refactors reference tests that must not exist by then).

---

### F-2 (H-6) — UI gap: `RAG_USER_COLLECTION_ENABLED` has no admin control

**Severity**: High
**Blast radius**: smallest-isolated (single Svelte component)
**Files in scope**:

- `src/lib/components/admin/Settings/Documents.svelte` (ADD one Switch control near existing RAG toggles)

**Observation from review**: `backend/open_webui/routers/retrieval.py` exposes 11 user-facing fields in `get_rag_config`/`update_rag_config`, one of which — `RAG_USER_COLLECTION_ENABLED` — has no UI control despite being default-on and affecting per-user vector-collection behaviour.

**Behavioural requirement**: admin can toggle per-user collection storage on/off from the Documents settings page, and the round-trip persists through `GET/POST /api/retrieval/config`.

**Acceptance criteria**:

- A labelled Switch exists in `Documents.svelte` with a stable test selector (e.g. `data-testid="rag-user-collection-enabled-switch"` or matching the existing convention for other switches in that file).
- Toggling the Switch and saving issues the same `PUT /api/retrieval/config` shape with `RAG_USER_COLLECTION_ENABLED: <bool>` in the body.
- The value is restored on page reload from `GET /api/retrieval/config`.
- Either the existing `e2e/tests/admin-rag-settings.spec.ts` is extended to cover this control, or a new spec file exercises it end-to-end.

**Non-deferrable**: yes (backend already exposes it; silent-default is a surprise vector for operators).

---

## Deferred Items Assigned To This Wave

None.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/utils/research.py` (DELETE ONLY)
  - `backend/open_webui/test/utils/test_*research*.py` (DELETE ONLY — grep to find all; there should be 4)
  - Any backend caller importing from `utils.research` (imports removal only)
  - `src/lib/components/admin/Settings/Documents.svelte` (ADD Switch + binding + i18n key)
  - Related i18n string files for the new label (only keys for the new Switch)
  - `e2e/tests/admin-rag-settings.spec.ts` (EXTEND to cover the new Switch) — OR a new spec file if kind 1 judges it cleaner
- **Forbidden files**:
  - Everything under `.agent-team-waves/archive-*/` (historical, untouchable)
  - Any file touched by F-3 through F-11 (see spec for list) — those are other waves' scope
  - `backend/open_webui/utils/middleware.py` (touched by W3/W4; no edits here)
  - `backend/open_webui/routers/skills.py` (touched by W2)
  - `backend/open_webui/retrieval/loaders/kg1.py` (touched by W2)
  - `backend/open_webui/routers/retrieval.py` (touched by W2 for F-5; if W1 needs to inspect it for F-2 UI contract, read-only)
- **Out-of-scope items (deferred to future waves)**:
  - F-3, F-4, F-5 → Wave 2
  - F-6, F-7 → Wave 3
  - F-8, F-9 → Wave 4
  - F-10, F-11 → Wave 5
  - Every Medium/Low/Nit/Test-gap/Architecture observation in the review (out of scope for this run entirely)

## Handoff from Wave 0

This is the first wave. No prior wave context.

Relevant baseline (from scoping review, not from a prior executed wave):

- Branch `feat/v0.8.12-hermes-port` at commit `64574ab25` is the starting point.
- Forensic review of 11 commits `9bd84258d..HEAD` produced the findings this run fixes.
- Prior-run wave artefacts archived at `.agent-team-waves/archive-2026-04-18-review-fix-source/`.

## Success Criteria

Each criterion must be independently testable by kind 2.

1. `rg -l 'utils\.research|utils/research' backend/ src/` returns zero paths outside `.agent-team-waves/archive-*/` and outside docs.
2. `uv run python -c "import open_webui.utils"` exits 0.
3. `uv run pytest backend/open_webui/test/` exits 0. No "module not found" collection errors related to the deleted research tests.
4. Grep confirms `test_middleware_research.py` and the other `test_*research*.py` files are removed (count: 4, per memory and review; kind 1 MUST grep-find and confirm the actual count before deleting).
5. `Documents.svelte` contains one new Switch control for `RAG_USER_COLLECTION_ENABLED` with:
   - a human-readable label,
   - a stable test selector,
   - two-way binding to the settings model,
   - placement consistent with other RAG toggles in the same component.
6. `bun run check` exits 0.
7. `bun run format` produces no diff (Prettier-clean).
8. One of:
   - `e2e/tests/admin-rag-settings.spec.ts` is extended with a test that toggles the new Switch, saves, reloads, and asserts persistence — and the extension passes locally via `bun run test:e2e` (or equivalent) OR
   - a new spec file `e2e/tests/admin-rag-user-collection-switch.spec.ts` does the same.
9. No file listed under "Forbidden files" is modified in this wave.
10. No finding outside F-1 and F-2 is touched in this wave (enforced by the scope contract).

## Notes for principal-engineer (kind 1)

- F-1 is a pure deletion. Do NOT try to "preserve optionality" by commenting out imports or leaving behind skeleton files. Delete cleanly.
- F-2: follow the existing Switch patterns in `Documents.svelte` (there are ~10 nearby). Match the existing i18n + binding convention, do not invent a new one.
- Revisit is T1-only here, so the revisit scope does NOT apply to this turn. T2 and T3 will carry the revisit + cross-validation scopes per the T0 plan that kind 3 will issue.

## Notes for evaluator (kind 2)

- Independently grep for any `research` import — do not trust kind 1's claim.
- Run the full backend test suite, not just the deleted area.
- For F-2: use `playwright-cli` to visually confirm the Switch is rendered, clickable, and the saved value survives a page reload.
- Do NOT open scope into Medium/Low findings from the review — this wave has a narrow contract.

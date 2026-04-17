# Wave 2 — Reveal Packet

**Wave Objective**: Add three Playwright smoke tests (image upload four-quadrants, ZIP skill import, RAG admin-settings round-trip) plus one new helper and one binary fixture, so the features delivered in the previous /atw's Waves 1-3 have real-browser coverage. Structure-only gate: `bunx playwright test --list` collects the 3 new specs without parse errors.
**Data-flow segment**: output (end-to-end behavioural verification)
**Blast radius**: moderate
**Total waves**: 2 of 2

## Spec Slice (from qa-planner memo, verbatim)

From `/Users/noelbao/.claude-kg/plans/dynamic-snacking-sprout.md`:

> **The gaps (relevant to Wave 2):**
>
> 3. **Zero real-browser coverage** for the 3 new user-facing flows delivered in Waves 1-3: four-quadrant image upload, ZIP skill import, RAG admin settings round-trip. 17 backend/unit tests cover the backend contracts but no test has actually clicked through the UI.

> ### Wave 2 — Playwright smoke tests for Wave 1-3 UI features
>
> **Blast radius:** moderate (3 new spec files, fixture assets)
> **Data-flow segment:** output (end-to-end behavioral verification)
>
> | Turn | Primary Scope | Revisit | Lens | Notes |
> |------|---------------|---------|------|-------|
> | T1 | Create `e2e/tests/image-upload.spec.ts` covering four quadrants: (vision × enabled/disabled) × (non-vision × enabled/disabled). Admin toggle via API call before each test (`updateRAGConfig`). Use existing `auth.ts` + `chat.ts` helpers. Login with `admin@localhost` / `admin`. | — | — | Hardest test — requires admin config manipulation + model selection |
> | T2 | Create `e2e/tests/skill-zip-import.spec.ts`: upload a fixture ZIP containing `SKILL.md`, assert list refresh + success toast. Fixture lives at `e2e/fixtures/test-skill.zip`. Negative test: invalid ZIP missing `SKILL.md` shows error toast. | T1 | contract-alignment | Revisit verifies test patterns are consistent between specs |
> | T3 | Create `e2e/tests/admin-rag-settings.spec.ts`: set all 10 new controls to non-default values via UI, save, reload page, assert values preserved. Full cross-validation + running all 3 new specs via `bun run test:e2e`. | T1, T2 | global-consistency | |
>
> **Files modified:**
> - `e2e/tests/image-upload.spec.ts` (new, ~150 lines)
> - `e2e/tests/skill-zip-import.spec.ts` (new, ~80 lines)
> - `e2e/tests/admin-rag-settings.spec.ts` (new, ~120 lines)
> - `e2e/fixtures/test-skill.zip` (new, binary fixture)
> - `e2e/helpers/admin.ts` (new, admin login + config manipulation helpers, ~40 lines)

> ## Critical Reuse — Existing Code and Patterns
>
> - **Existing auth helper**: `e2e/helpers/auth.ts` — `DEFAULT_CREDENTIALS: test@example.com / test123` (may need to add admin variant)
> - **Existing chat helpers**: `e2e/helpers/chat.ts` — `selectModel`, `sendMessage`, `uploadFile`, `waitForResponse` — reuse verbatim
> - **Existing RAG config API**: `src/lib/apis/retrieval/index.ts` (`getRAGConfig`, `updateRAGConfig`) — callable from test setup
> - **Skill upload API**: `src/lib/apis/skills/index.ts::uploadSkillZip` — test can use directly or go through UI
> - **Test fixtures dir pattern**: `e2e/fixtures/` (new)

> ## Risks
>
> 1. **Playwright tests need a running backend on port 8083**. Evaluation in the ATW harness will verify test STRUCTURE (compiles, playwright collects tests), not that they pass against live services. The user runs them manually afterward.
> 2. **Admin credentials may differ in user's environment**. Plan assumes `admin@localhost` / `admin` (default when `WEBUI_AUTH=False`). Test setup helper should accept env-var overrides (`ADMIN_EMAIL`, `ADMIN_PASSWORD`).
> 3. **ZIP fixture authoring**: the test-skill.zip must conform to the SKILL.md format accepted by `routers/skills.py:289-295`.
> 4. **Four-quadrant admin toggling**: each quadrant requires flipping `IMAGE_ANALYSIS_ENABLED` via API before the test. Tests must restore original state in `afterEach` to avoid cross-test pollution.

## Deferred Items Assigned To This Wave

None.

## Constraints for This Wave

- **Allowed files to create**:
  - `e2e/tests/image-upload.spec.ts`
  - `e2e/tests/skill-zip-import.spec.ts`
  - `e2e/tests/admin-rag-settings.spec.ts`
  - `e2e/helpers/admin.ts`
  - `e2e/fixtures/test-skill.zip` (binary) and any supporting scratch files needed to build it (the fixture builder script may live under `e2e/fixtures/` too)
- **Allowed files to modify**: `e2e/helpers/auth.ts` may receive an additional export for admin login variant IF an ergonomic override isn't possible through pure composition. Prefer leaving it untouched and wrapping it in `e2e/helpers/admin.ts`.
- **Forbidden files**:
  - Any `src/*` frontend file (Waves 1-3 of the prior run are locked — any regression would force re-evaluation)
  - Any backend production code (routers, config, pipes, middleware)
  - `package.json`, `package-lock.json`
  - Existing specs `e2e/tests/*.spec.ts` (no modifications)
  - `e2e/playwright.config.ts` (no modifications)
- **Out-of-scope items (deferred)**: none — this is the last wave of the run

## Handoff from Wave 1

Wave 1 closed COMPLETE at commit `10da77db9` on `feat/v0.8.12-hermes-port`. Deliverables:

- `backend/open_webui/pipes/hermes_agent.py:209-216` — dead `reasoning_content` branch removed; try body collapsed to `chunk = json.loads(data_str); yield chunk`.
- `backend/open_webui/pipes/hermes_agent.py:94-121` — docstring extended with "Connection vs. pipe" paragraph enumerating the 4 pipe-only features.
- `backend/open_webui/test/utils/test_hermes_pipe.py:156-197` — new regression test `test_pipe_passes_through_reasoning_content_unchanged`. Full pytest gate: 18 passed.

Wave 1 is unrelated to Wave 2's scope and contains no dependencies for this wave. Wave 2 stands alone as e2e test authoring.

## Code anchors you will rely on

### Existing Playwright infra (do not modify — reuse)

- **`e2e/playwright.config.ts`** — `baseURL: http://localhost:8083` (overridable via `BASE_URL` env); `headless` overridable via `HEADED=1`; `fullyParallel: false`, `workers: 1` (sequential execution is mandatory — tests share server state).
- **`e2e/helpers/auth.ts:12-15`** — `DEFAULT_CREDENTIALS = { email: 'test@example.com', password: 'test123' }`.
- **`e2e/helpers/auth.ts:51-80`** — `login(page, email?, password?)` sets `localStorage.locale = 'en-US'`, navigates to `/auth`, fills credentials, waits for `#chat-input`, dismisses version dialog. Accepts optional email/password — can be called with admin credentials directly.
- **`e2e/helpers/chat.ts:42-66`** — `selectModel(page, modelId)`.
- **`e2e/helpers/chat.ts:117-150`** — `uploadFile(page, filePath, timeout?)`.
- **`e2e/tests/file-upload.spec.ts`** — 53-line example of the upload-PDF test pattern. Use as stylistic reference.

### Admin creation (backend default)

- **`backend/open_webui/routers/auths.py:587-604`** — when `WEBUI_AUTH=False`, backend auto-creates admin with `admin_email = 'admin@localhost'` and `admin_password = 'admin'` on first request. `e2e/helpers/admin.ts` should read `ADMIN_EMAIL` and `ADMIN_PASSWORD` env vars and fall back to these defaults.

### Skill ZIP upload endpoint

- **Backend validator** — `backend/open_webui/routers/skills.py:233-303`:
  - POST `/api/v1/skills/upload-zip` accepts `multipart/form-data` with field name `file`
  - `content_type` must be `application/zip` or filename endswith `.zip`
  - `MAX_ZIP_SIZE` check (both compressed and uncompressed)
  - `zipfile.is_zipfile(...)` sanity check
  - Must contain `SKILL.md` at zip root OR at first-level subdirectory (iterates `extract_path.iterdir()` once)
  - `_parse_skill_md_frontmatter` expects YAML-like frontmatter with `name:` and optional `description:`
  - 400 responses: `'Only .zip files are accepted'`, `'Invalid zip file'`, `'No SKILL.md found in zip archive (checked root and one level deep)'`, `'File too large'`, `'Uncompressed size too large'`, `ID_TAKEN` (duplicate skill_id)
- **Frontend API** — `src/lib/apis/skills/index.ts:3-30`:
  - `uploadSkillZip(token, file)` — `POST /api/v1/skills/upload-zip` with `FormData` (`file` field), `Authorization: Bearer ${token}`.
  - `getSkills(token)` — `GET /api/v1/skills/` — lists skills; used to verify the new skill appears.
- **UI entry point** — `Skills.svelte:236-246` (per prior Wave 2 of the main run) routes `.zip` files to `uploadSkillZip` and then refreshes the list.

### RAG config API

- **Frontend API** — `src/lib/apis/retrieval/index.ts`:
  - Line 3: `getRAGConfig(token)` — `GET /api/v1/retrieval/config`
  - Line 63: `updateRAGConfig(token, payload: RAGConfigForm)` — `POST /api/v1/retrieval/config/update`
  - Line 156 / 202: `getEmbeddingConfig(token)` / `updateEmbeddingConfig(token, payload)` (for the 3 embedding prefix fields)
- **Image analysis toggle** — `IMAGE_ANALYSIS_ENABLED` in the RAG config payload. Flip via `updateRAGConfig(token, { ...existing, IMAGE_ANALYSIS_ENABLED: true|false })` before each quadrant test.
- **The 10 new controls covered by the prior Wave 3** (must be exercised by `admin-rag-settings.spec.ts`):
  1. `RAG_EMBEDDING_QUERY_PREFIX` (embedding config)
  2. `RAG_EMBEDDING_CONTENT_PREFIX` (embedding config)
  3. `RAG_EMBEDDING_PREFIX_FIELD_NAME` (embedding config)
  4. `RAG_FULL_DOCUMENT_CONTEXT` (switch)
  5. `RAG_FULL_DOCUMENT_MAX_TOKENS` (number, conditional)
  6. `RAG_SUBCHAT_CONCURRENCY` (number)
  7. `RAG_USER_COLLECTION_ENABLED` (switch)
  8. `RAG_DOCUMENT_INDEX_GENERATION` (switch)
  9. `RAG_DOCUMENT_INDEX_MODEL` (text, conditional)
  10. `RAG_DOCUMENT_INDEX_TIMEOUT` (number, conditional)

### Admin Svelte routes

- `/admin/settings/documents` — the RAG config page where the 10 new controls live (Documents.svelte `1315-1379` and `1668-1728`).
- `/workspace/skills` — the skills list page where ZIP upload lives.

## Success Criteria

1. **`e2e/helpers/admin.ts` exists** and exports at minimum:
   - `ADMIN_CREDENTIALS` — reads `ADMIN_EMAIL`/`ADMIN_PASSWORD` from env, defaults to `admin@localhost`/`admin`
   - `loginAsAdmin(page)` — wraps the existing `login()` with admin credentials
   - `getRAGConfigViaApi(page)` and `updateRAGConfigViaApi(page, payload)` — helpers that call `/api/v1/retrieval/config` and `.../config/update` using the logged-in session token. (Reading the token from `localStorage` is acceptable.)

2. **`e2e/tests/image-upload.spec.ts`** exists with four tests covering the quadrants (vision × enabled/disabled) × (non-vision × enabled/disabled). Each test:
   - Logs in as admin
   - Flips `IMAGE_ANALYSIS_ENABLED` to the required value via `updateRAGConfigViaApi`
   - Selects a model (vision-capable OR non-vision)
   - Attempts image upload
   - Asserts the expected UX (upload succeeds when permitted; blocked/error state when not permitted)
   - Restores the original `IMAGE_ANALYSIS_ENABLED` in `afterEach`
   - **Vision model IDs** are env-configurable — define `TEST_VISION_MODEL` and `TEST_NON_VISION_MODEL` at the top of the spec as `process.env.TEST_VISION_MODEL ?? '<sensible default>'`. Default values must be written but marked as environment-overridable.

3. **`e2e/tests/skill-zip-import.spec.ts`** exists with at least two tests:
   - Positive: upload `e2e/fixtures/test-skill.zip` → success toast/notification appears AND the new skill appears in the skills list (call `getSkills(token)` or read the UI list).
   - Negative: upload a malformed ZIP (e.g., a ZIP with no `SKILL.md`) → error toast with text mentioning "No SKILL.md" or similar. Use a second fixture or create the malformed ZIP inline via Node `zipfile` utilities.
   - `afterEach` must clean up any created skill via `deleteSkillById` to keep tests idempotent.

4. **`e2e/tests/admin-rag-settings.spec.ts`** exists and exercises all 10 new controls:
   - Navigates to `/admin/settings/documents`
   - Sets each control to a non-default value through the UI (click switches, fill numbers/text)
   - Saves (clicks the "Save" button)
   - Reloads the page or re-navigates
   - Reads each control's state and asserts the non-default values persisted
   - `afterEach` restores original values via `updateRAGConfigViaApi` + `updateEmbeddingConfigViaApi` (or uses `test.beforeEach` snapshot + `test.afterEach` restore pattern)

5. **`e2e/fixtures/test-skill.zip`** exists as a binary file. Contents: at least `SKILL.md` with valid frontmatter (`name: Test Skill`, optional `description`). May include additional files inside. Must be < MAX_ZIP_SIZE (default 10MB in routers/skills.py — verify the constant).

6. **Structure-only gate** (final T3 verification): `bunx playwright test --config=e2e/playwright.config.ts --list` must show the 3 new spec files collected with all tests enumerated, no parse errors. This is an authoritative success signal — the /atw harness does NOT start a live backend to actually run these tests.

7. **No regressions** to existing e2e specs: `bunx playwright test --config=e2e/playwright.config.ts --list 2>&1 | grep -E '\\.spec\\.ts'` still shows the existing 8 specs plus the 3 new ones.

8. **No forbidden file modifications**: `git diff --name-only HEAD | rg -v '^(e2e/|\\.agent-team-waves/)'` must return nothing production-affecting. Meta-infra files (`.gitignore`, `.webui_secret_key` pre-existing dirt) are acceptable if unchanged.

## Evaluator notes

- The evaluator (kind 2) cannot run a live backend in the /atw harness, so live browser assertions are out of scope for this wave's gate. The evaluator MUST independently verify:
  - Spec files PARSE and COLLECT via `bunx playwright test --list`
  - Test assertions reference selectors/APIs that actually exist in the codebase (cross-check against `src/lib/apis/retrieval/index.ts`, `src/lib/apis/skills/index.ts`, and any page-object selectors)
  - No forbidden-file modifications
  - The ZIP fixture is a real valid ZIP (`unzip -l e2e/fixtures/test-skill.zip`)
  - Env-var override pattern exists for admin credentials and vision model IDs
- The evaluator MAY (but is not required to) use the `playwright-cli` skill to attempt real browser verification IF a dev server happens to be reachable, but this is not the primary gate. The primary gate is STRUCTURE + CODE-LEVEL CORRECTNESS.

## Junior delegation guidance (kind 3 will expand this at T0)

- The ZIP fixture may be built by haiku-coder-junior given an exact manifest (files + contents).
- The `playwright test --list` run may be delegated to haiku-test-junior.
- Decisions about selector choice, test boundary, and API vs UI path for state setup must stay with kind 1.

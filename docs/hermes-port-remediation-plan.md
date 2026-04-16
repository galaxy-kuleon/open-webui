# Hermes Port Remediation Plan

## Goal

Make the `feat/v0.8.12-hermes-port` outcome behaviorally match:

`A` from `e4e69a10ec08a725bf2ab3db499ef664f2bd7570..b7793f72918803bcc2ddbe39de8b06213a601b31`

plus

Hermes integration sourced from `/Users/noelbao/Works/hermes-agent/`

without carrying silent regressions or half-ported UI/backend wiring.

## Current Verdict

The port is close, but it is not equivalent yet.

Main gaps:

1. Hermes session continuity is broken in the default no-auth setup.
2. Image-analysis support is only partially ported.
3. Several `A` admin/document settings exist in backend config but are missing or only partially wired in the UI.
4. Agent-skill ZIP import is only partially ported.
5. File-processing progress UX is only partially ported.
6. There is at least one extra regression not explained by Hermes integration: terminal selection persistence drift.
7. The Hermes-specific bootstrap path has no visible coverage.

## Success Criteria

The branch is done when all of the following are true:

1. A default local Hermes setup does not 403 on ordinary chat usage.
2. Features shipped in `A` are reachable end to end in the `v0.8.12` port, not just present in backend code.
3. New Hermes integration behavior is covered by focused tests.
4. Frontend settings round-trip cleanly to backend config.
5. Package metadata and lockfiles are internally consistent.
6. No extra regressions remain from the porting effort.

## Priority Levels

- `P0`: parity blocker or broken core behavior
- `P1`: required to honestly claim `B = A + Hermes`
- `P2`: hardening and cleanup that should land with the port if practical

## Workstream 1: Fix Hermes Session Continuity Contract

Priority: `P0`

### Problem

`backend/open_webui/pipes/hermes_agent.py` always forwards `X-Hermes-Session-Id` when Open WebUI passes `chat_id`.

Hermes itself rejects session continuation unless API-key auth is configured and used:

- `backend/open_webui/pipes/hermes_agent.py`
- `backend/open_webui/functions.py`
- `/Users/noelbao/Works/hermes-agent/gateway/platforms/api_server.py`

This makes the current default valve configuration internally inconsistent:

- default `hermes_api_key = ""`
- session header still sent
- Hermes returns `403` for continuation

### Required Fix

Adapt the Open WebUI Hermes client to Hermes' actual server contract. Do not weaken the Hermes-side security gate.

### Implementation Tasks

1. In `backend/open_webui/pipes/hermes_agent.py`, only send `X-Hermes-Session-Id` when authenticated session continuation is actually supported.
2. Treat `hermes_api_key` presence as the minimum condition for sending the continuation header.
3. Keep stateless streaming working when no API key is configured.
4. Improve error messaging so a `403` clearly tells the operator that session continuity requires `API_SERVER_KEY` plus a matching `hermes_api_key`.
5. Update the pipe docstring/valve descriptions to reflect the real contract.

### Acceptance Criteria

1. Hermes chat works in single-turn mode with default local settings and no API key.
2. Hermes chat works in multi-turn mode when both sides are configured for API-key auth.
3. No normal local chat path silently fails with a Hermes `403`.

### Test Plan

1. Add a focused test for the pipe request-builder behavior:
   - no API key -> no `X-Hermes-Session-Id`
   - API key present -> `X-Hermes-Session-Id` forwarded
2. Add an error-path test for Hermes `403` translation.
3. Manual smoke test against a local Hermes server with and without `API_SERVER_KEY`.

## Workstream 2: Restore Image Analysis End-to-End

Priority: `P0`

### Problem

`B` carries image-analysis backend/config code, but the feature is not fully reachable:

- `backend/open_webui/utils/image_analysis.py` exists
- `backend/open_webui/config.py` exposes toggles
- `src/lib/components/admin/Settings/Documents.svelte` exposes an Image Analysis section

But:

- `backend/open_webui/routers/files.py` does not actually invoke `analyze_image`
- `src/lib/components/chat/MessageInput.svelte` still blocks image upload when selected models are non-vision

So the feature shipped in `A` is effectively dead in `B`.

### Required Fix

Reconnect the upload pipeline and frontend gating so image analysis really enables non-vision workflows.

### Implementation Tasks

1. In `backend/open_webui/routers/files.py`, restore the image-analysis branch so image uploads call `analyze_image(...)` when enabled.
2. Preserve the intended routing logic:
   - speech/audio -> transcription path
   - image + analysis enabled -> analysis path
   - other extractable docs -> normal `process_file(...)`
3. In `src/lib/components/chat/MessageInput.svelte`, change image upload gating:
   - if image analysis is enabled and configured, allow upload even when selected models are non-vision
   - only hard-block when neither vision support nor image analysis support is available
4. Ensure UX messaging matches actual behavior.
5. Confirm analyzed image content lands where downstream RAG / prompt injection expects it.

### Acceptance Criteria

1. With Image Analysis enabled and configured, non-vision model chats can upload images successfully.
2. The backend stores OCR/description output into the file record as intended.
3. Downstream chat behavior uses the analyzed content instead of rejecting the image.
4. With Image Analysis disabled, current vision-only restrictions still behave correctly.

### Test Plan

1. Add backend coverage for the image upload branch in `routers/files.py`.
2. Add a focused test for the image-analysis enable/disable conditions.
3. Add a UI/e2e check for:
   - non-vision + image analysis enabled -> allowed
   - non-vision + image analysis disabled -> blocked

## Workstream 3: Restore Admin RAG Settings Parity

Priority: `P1`

### Problem

Several `A` settings are present in backend config and retrieval endpoints but are missing or only partially wired in the admin UI.

Examples:

- `RAG_EMBEDDING_QUERY_PREFIX`
- `RAG_EMBEDDING_CONTENT_PREFIX`
- `RAG_FULL_DOCUMENT_CONTEXT`
- `RAG_FULL_DOCUMENT_MAX_TOKENS`
- `RAG_SUBCHAT_CONCURRENCY`
- `RAG_DOCUMENT_INDEX_GENERATION`
- `RAG_DOCUMENT_INDEX_MODEL`
- `RAG_DOCUMENT_INDEX_TIMEOUT`
- `RAG_USER_COLLECTION_ENABLED`

There is also a partial UI round-trip bug:

- `src/lib/components/admin/Settings/Documents.svelte` declares embedding prefix state
- `backend/open_webui/routers/retrieval.py` accepts and persists those values
- the frontend update payload currently does not send them

### Required Fix

Bring the admin Documents settings page back to true `A` parity for the features already ported server-side.

### Implementation Tasks

1. In `src/lib/components/admin/Settings/Documents.svelte`, include embedding prefixes in the payload sent by `updateEmbeddingConfig(...)`.
2. Restore the missing admin controls for:
   - Full Document Context
   - Max Document Tokens
   - Sub-Chat Concurrency
   - Document Index Generation
   - Index Generation Model
   - Index Generation Timeout
   - User Collection
3. Verify values are loaded from backend config into UI state on page load.
4. Verify save operations update both in-memory app state and persistent config storage.
5. Audit any other `A`-era retrieval settings now present in backend but not surfaced in the page.

### Acceptance Criteria

1. Every backend retrieval/config feature ported from `A` and intended for admin control is visible and editable in the Documents settings UI.
2. Saving the page actually updates those backend values.
3. Reloading the page preserves the saved values.

### Test Plan

1. Add frontend-level coverage for payload composition if there is an existing pattern for it.
2. Add API tests for config round-trip if missing.
3. Manual admin smoke test of the Documents page covering each restored toggle/field.

## Workstream 4: Restore Agent Skill ZIP Import End-to-End

Priority: `P1`

### Problem

The backend and API helper exist:

- `backend/open_webui/routers/skills.py` has `/skills/upload-zip`
- `src/lib/apis/skills/index.ts` has `uploadSkillZip(...)`

But the visible Skills UI still only accepts `.md,.json`, so ZIP import is unreachable from the app.

### Required Fix

Reconnect the Skills UI to the already-ported backend/API path.

### Implementation Tasks

1. In `src/lib/components/workspace/Skills.svelte`, change the file input accept list to include `.zip`.
2. Add the ZIP import branch in the `on:change` handler.
3. Call `uploadSkillZip(...)` for ZIP imports.
4. Refresh skill list state after successful import.
5. Preserve existing JSON and Markdown import behavior.
6. Confirm imported agent skills show the existing `Agent` badge and can be edited/executed as expected.

### Acceptance Criteria

1. Admin or permitted user can import an agent-skill ZIP from the UI.
2. Imported skill appears immediately in the list.
3. Existing JSON/Markdown import paths still work.

### Test Plan

1. Add frontend coverage for ZIP branch selection if practical.
2. Add an e2e import flow for a small valid agent-skill ZIP.
3. Add negative-path checks:
   - invalid zip
   - missing `SKILL.md`
   - ID collision

## Workstream 5: Restore File-Processing Progress UX

Priority: `P1`

### Problem

The file API supports progress callbacks, and `A` added richer status states, but `B` only partially carries the UX:

- `src/lib/apis/files/index.ts` supports `onProgress`
- `src/lib/components/common/FileItem.svelte` supports `statusText`
- current `MessageInput.svelte` path still calls `uploadFile(...)` without a progress callback
- loading UI still only checks `status === 'uploading'`

Result: users lose extraction/indexing/embedding/quick-mode feedback that `A` intended.

### Required Fix

Reconnect progress streaming to the UI and map backend statuses to readable labels.

### Implementation Tasks

1. In `src/lib/components/chat/MessageInput.svelte`, pass an `onProgress` callback to `uploadFile(...)`.
2. Update file chips/cards so loading covers streamed processing states, not just `uploading`.
3. Restore human-readable status labels for states like:
   - extracting
   - indexing
   - embedding
   - quick_mode
4. Audit any other upload entrypoints besides `MessageInput.svelte` so all user-visible paths stay consistent.

### Acceptance Criteria

1. Users can see upload progress transition through processing states.
2. Progress display remains correct for normal extraction, skip-rag/quick mode, and indexing-heavy flows.
3. No stale loading state remains after processing completes.

### Test Plan

1. Manual smoke test with doc upload, image analysis upload, and skip-rag upload.
2. Add frontend coverage for status mapping if there is an existing UI test pattern.

## Workstream 6: Resolve Terminal Selection Persistence Drift

Priority: `P1`

### Problem

There is extra drift in `B` unrelated to Hermes:

- `src/lib/components/chat/Chat.svelte` still restores terminal enabled state from `selectedTerminalId`
- `src/routes/(app)/+layout.svelte` no longer persists/restores `selectedTerminalId`

That leaves the codebase internally inconsistent.

### Required Fix

Decide whether terminal selection should persist across reloads. Then make the codebase consistent with that decision.

### Implementation Tasks

1. Confirm intended `v0.8.12` behavior:
   - persistent selection across reloads
   - or session-only selection
2. If persistence is intended:
   - restore the `localStorage.selectedTerminalId` sync in `src/routes/(app)/+layout.svelte`
3. If persistence is not intended:
   - remove stale restore logic and any assumptions in `Chat.svelte` and related components
4. Validate both direct and system terminal paths.

### Acceptance Criteria

1. Terminal selection behavior is explicit and consistent across the codebase.
2. Reload behavior matches the intended product behavior.
3. No dead persistence code remains.

### Test Plan

1. Manual test for direct terminal selection across reload.
2. Manual test for system terminal selection across reload.

## Workstream 7: Lockfile and Packaging Consistency

Priority: `P1`

### Problem

`B` carries `package.json` changes for Playwright and build behavior, but not the corresponding `package-lock.json` update that `A` carried.

This leaves install reproducibility and branch hygiene in a bad state.

### Required Fix

Regenerate and commit the lockfile that matches the declared package metadata.

### Implementation Tasks

1. Update `package-lock.json` to match the current `package.json`.
2. Verify `@playwright/test` and any related resolution changes are correctly captured.
3. Confirm the `build` script copy step is intentional and still valid on `v0.8.12`.

### Acceptance Criteria

1. `package.json` and `package-lock.json` are in sync.
2. Fresh install does not mutate the lockfile.
3. CI and local frontend commands use a consistent dependency graph.

### Test Plan

1. Run lockfile regeneration and confirm no follow-up diff remains.
2. Run the relevant frontend install/build command once after regeneration.

## Workstream 8: Add Hermes Integration Coverage

Priority: `P2`

### Problem

Hermes integration currently introduces new behavior with little or no visible regression coverage:

- builtin pipe bootstrap
- Hermes manifold discovery
- Hermes SSE event translation
- auth-gated session continuity

### Required Fix

Add focused coverage around the new Hermes-specific surface so the integration stops being fragile.

### Implementation Tasks

1. Add tests for `backend/open_webui/utils/builtin_pipes.py`:
   - no users -> no registration
   - first user exists -> registration
   - existing builtin with matching hash -> no write
   - content hash change -> update
2. Add tests for Hermes manifold discovery in `backend/open_webui/pipes/hermes_agent.py`.
3. Add tests for custom SSE event translation:
   - `hermes.tool.progress` -> Open WebUI status events
4. Add tests for reasoning/status emission behavior if retained.

### Acceptance Criteria

1. Hermes pipe registration and streaming behavior are covered by targeted tests.
2. Future changes to the pipe or bootstrap code fail loudly in CI if contracts break.

## Recommended Execution Order

1. Workstream 1: Hermes session continuity
2. Workstream 2: image analysis end-to-end
3. Workstream 4: agent skill ZIP import
4. Workstream 3: admin RAG settings parity
5. Workstream 5: file-processing progress UX
6. Workstream 6: terminal persistence drift
7. Workstream 7: lockfile/package consistency
8. Workstream 8: Hermes-specific coverage and hardening

Reason for this order:

1. Fix the true parity blockers and live breakages first.
2. Restore visibly missing user-facing `A` features next.
3. Clean up secondary drift after feature parity is back.
4. Land coverage after behavior stabilizes, or in parallel where safe.

## Verification Matrix

### Hermes Integration

1. Local Hermes, no API key:
   - model discovery works
   - chat works
   - no session-continuation 403s
2. Local Hermes, API key enabled:
   - multi-turn continuity works
   - tool progress renders

### Agent Skills

1. ZIP import from UI works
2. Imported skill shows in list
3. Keyword intercept still works
4. Work-dir and sandbox modes still work

### Files and RAG

1. Standard doc upload shows progress and completes
2. Skip-rag upload shows quick-mode path correctly
3. Image upload:
   - vision models still work
   - non-vision models + image analysis enabled work
4. Retrieval settings save, reload, and affect runtime behavior

### Frontend Drift

1. Terminal selection behavior is consistent after reload
2. Skills page import flows all still work

### Packaging

1. Fresh install leaves no lockfile diff
2. Build/test scripts still work

## Definition Of Done

This remediation is complete only when:

1. The known parity gaps above are closed.
2. The Hermes integration is not broken in the default supported setup.
3. The `A` features that were ported server-side are reachable from the UI.
4. The branch no longer carries unexplained drift unrelated to Hermes.
5. Focused regression coverage exists for the new Hermes-specific surface.

## Notes

This file is a remediation plan only. It intentionally does not prescribe the exact patch shape beyond the contract and acceptance criteria, so implementation can stay aligned with the current `v0.8.12` architecture instead of force-fitting `v0.8.10` code literally.

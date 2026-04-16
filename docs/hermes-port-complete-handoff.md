# Hermes Port Complete Handoff

## Read This First

This document is intended to be sufficient for the next engineer to continue the work without asking the previous engineer follow-up questions.

There is no hidden context outside:

- this file
- `docs/hermes-port-remediation-plan.md`
- the current git diff in this worktree

If this document and the repo disagree, trust the repo.

## Problem Definition

User-defined terms:

- `A` = changes between commit `e4e69a10ec08a725bf2ab3db499ef664f2bd7570` and `b7793f72918803bcc2ddbe39de8b06213a601b31`
- `B` = changes between commit `9bd84258d09eefe7bf975878fb0e31a5dadfe0f8` and `3e3f93fbfb761aa31a28d165d9a4d78207bbd29f`

Expected equation:

`A + Hermes Agent integration = B`

Hermes source of truth:

- `/Users/noelbao/Works/hermes-agent/`

## Review Verdict

The equation is still false.

Closest honest description:

`B ≈ most of A ported onto v0.8.12 + Hermes pipe wiring - several A UI/wiring pieces + at least one unrelated regression`

Main review findings:

1. Hermes session continuity was broken in the default no-auth setup because Open WebUI always forwarded `X-Hermes-Session-Id`, while Hermes only accepts session continuation on authenticated requests.
2. Image analysis from `A` was only partially ported: backend utility/config existed, but the upload pipeline and chat UI wiring were incomplete.
3. Some `A` RAG settings existed backend-side but were missing or only partially wired in the admin UI.
4. Agent skill ZIP import existed backend/API-side but was unreachable from the visible UI.
5. File-processing progress UX from `A` was only partially ported.
6. There was extra drift not explained by Hermes integration, including terminal selection persistence regression.
7. Hermes-specific coverage around the Open WebUI integration path was thin.

## Repository State

- Repo: `/Users/noelbao/Works/open-webui`
- Branch: `feat/v0.8.12-hermes-port`
- Date of this handoff: `2026-04-16`

Current related modified files:

- `backend/open_webui/pipes/hermes_agent.py`
- `backend/open_webui/routers/files.py`
- `src/lib/components/chat/MessageInput.svelte`

Current related untracked files:

- `backend/open_webui/test/utils/test_hermes_pipe.py`
- `backend/open_webui/test/utils/test_file_upload_image_analysis.py`
- `docs/hermes-port-remediation-plan.md`
- `docs/hermes-port-complete-handoff.md`

Current unrelated dirty files that were already present and should not be reverted as part of this work:

- `.gitignore`
- `.webui_secret_key`
- `backend/open_webui_data.zip`

At the time of writing, `git diff --stat` for the tracked files touched in this slice is:

```text
backend/open_webui/pipes/hermes_agent.py    | 30 +++++++++++++++++++++++----
backend/open_webui/routers/files.py         | 12 +++++++++++
src/lib/components/chat/MessageInput.svelte | 32 ++++++++++++++++++++++++-----
3 files changed, 65 insertions(+), 9 deletions(-)
```

Note: that diff stat does not include the new untracked tests/docs until they are staged.

## What Has Already Been Done

### 1. Wrote the remediation plan

File:

- `docs/hermes-port-remediation-plan.md`

Purpose:

- captures the full prioritized fix plan
- separates `P0`, `P1`, and `P2`
- includes acceptance criteria and test expectations

### 2. Fixed the Hermes session-continuity contract in the Open WebUI pipe

File:

- `backend/open_webui/pipes/hermes_agent.py`

Problem that existed:

- Open WebUI passed `chat_id`
- the Hermes pipe unconditionally mapped that to `X-Hermes-Session-Id`
- Hermes API server rejects session continuation unless the request is authenticated with `API_SERVER_KEY`
- result: ordinary local no-auth chats could hit a `403`

What changed:

1. Updated the `hermes_api_key` valve description to state that it is required for authenticated session continuity.
2. Updated the main `pipe()` docstring to explain the stateless fallback when no API key is configured.
3. Added `_maybe_add_session_header(self, headers, chat_id) -> bool`.
4. `pipe()` now calls `_maybe_add_session_header(...)` instead of always setting `X-Hermes-Session-Id`.
5. If there is no `hermes_api_key`, the pipe now skips the session header and logs that requests will rely on stateless request-body history.
6. If Hermes returns `403` while session continuity was attempted, the error text now explicitly tells the operator that Hermes needs `API_SERVER_KEY` and Open WebUI needs a matching `hermes_api_key`.

Important note:

- `backend/open_webui/functions.py` was part of the original problem analysis because it passes `chat_id`, but it did not need to be changed once the pipe itself became contract-aware.

Expected behavior after this change:

- no API key configured: no `X-Hermes-Session-Id`; requests fall back to stateless history in the request body
- API key configured: bearer auth is sent and `X-Hermes-Session-Id` is sent

### 3. Restored backend image-analysis dispatch

File:

- `backend/open_webui/routers/files.py`

Problem that existed:

- the image-analysis utility existed
- the admin/config surface existed
- the upload pipeline did not actually call `analyze_image(...)`

What changed:

1. In `process_uploaded_file(...)`, restored the image branch:
   - if `content_type.startswith('image/')`
   - and `request.app.state.config.IMAGE_ANALYSIS_ENABLED` is true
   - call `open_webui.utils.image_analysis.analyze_image(...)`
2. Left STT/audio routing intact.
3. Left normal document extraction routing intact.

Expected behavior after this change:

- when image analysis is enabled server-side, uploaded images now enter the image-analysis path instead of being dead code

### 4. Restored chat upload progress wiring and non-vision image upload path

File:

- `src/lib/components/chat/MessageInput.svelte`

What changed:

1. `uploadFile(...)` now receives a status callback so file chips can reflect backend processing stages while upload/extraction/indexing runs.
2. The non-vision hard block for image uploads was removed.
3. When no selected model is vision-capable, the UI now shows an informational toast instead of immediately rejecting the image.
4. Compressed images now go through `uploadFileHandler(compressedFile)` so they follow the same backend processing path as other uploads.
5. `FileItem` loading/status handling now recognizes:
   - `uploading`
   - `processing:extracting`
   - `processing:embedding`
   - `processing:indexing`
   - `processing:quick_mode`

Intent:

- restore the `A`-era progress UX
- make image uploads flow into the backend image-analysis path

Important implementation note:

- the newly added progress/status labels use plain strings, not `$i18n.t(...)`
- this was intentional to avoid adding more noise to the repo's already-broken Svelte/i18n typing state

### 5. Added focused backend regression tests

Files:

- `backend/open_webui/test/utils/test_hermes_pipe.py`
- `backend/open_webui/test/utils/test_file_upload_image_analysis.py`

Coverage added:

1. Hermes pipe does not send `X-Hermes-Session-Id` without `hermes_api_key`.
2. Hermes pipe does send `X-Hermes-Session-Id` when `hermes_api_key` is set.
3. Hermes pipe `403` path appends the operator-facing session-auth hint.
4. File upload processing dispatches images to `analyze_image(...)` when image analysis is enabled.

## What Is Fixed vs Not Fixed

### Fixed enough to be considered real progress

1. Hermes session-header contract bug is fixed on the Open WebUI side.
2. Backend image-analysis routing is restored.
3. Chat file-progress UX is restored for the main chat input path.
4. There is now targeted backend coverage for the Hermes and image-analysis fixes.

### Not fully done yet

1. The frontend image gating is not yet config-aware.
2. The RAG settings UI parity work is not done.
3. ZIP skill import UI parity is not done.
4. Terminal persistence regression is not fixed.
5. Lockfile/tooling parity is not fixed.
6. Manual Hermes smoke testing has not been completed.
7. Frontend repo-wide typecheck is still badly broken.

## Important Limitation In The Current P0 Slice

The image-upload behavior is improved but not fully correct yet.

Current behavior in `MessageInput.svelte`:

- if selected models are non-vision, the UI no longer blocks image upload
- it always allows the upload attempt and shows an informational toast

Current behavior in `backend/open_webui/routers/files.py`:

- image processing succeeds only if `IMAGE_ANALYSIS_ENABLED` is enabled server-side

Consequence:

- if a non-vision model is selected and server-side image analysis is disabled, the upload can still be attempted from the UI, but backend processing will not take the image-analysis branch

What still needs to happen:

- the frontend should only allow the non-vision image path when image analysis is actually enabled/configured
- otherwise it should still hard-block, or at minimum show a precise error before upload

This is the first thing to finish in the next work slice if the goal is to honestly claim `P0` image-analysis parity.

## Validation Already Run

### Backend tests

Command:

```bash
env PYTHONPATH=backend .venv/bin/pytest \
  backend/open_webui/test/utils/test_hermes_pipe.py \
  backend/open_webui/test/utils/test_file_upload_image_analysis.py
```

Result:

- `5 passed`

Warnings seen:

- pytest-asyncio loop-scope deprecation warning
- peewee/sqlalchemy/alembic deprecation warnings
- pydantic deprecation warning
- pydub ffmpeg warning in one earlier run

These warnings did not fail the tests.

### Frontend typecheck

Command:

```bash
npm run check
```

Result:

- failed repo-wide
- `svelte-check found 9168 errors and 255 warnings in 364 files`

Interpretation:

- the frontend tree is already globally red
- `npm run check` is not currently a clean pass/fail signal for this branch

Important targeted observation:

- after replacing newly added `$i18n.t(...)` calls with plain strings, the narrowed grep against the touched `MessageInput.svelte` lines no longer showed new i18n store-type errors from this change
- the only targeted diagnostic still touching a newly edited line was:
  - `src/lib/components/chat/MessageInput.svelte:641:15`
  - `Variable 'files' implicitly has an 'any[]' type`

That issue is part of the component's existing broader typing debt, not a new architectural problem introduced by this slice.

### What has not been validated yet

1. No browser/manual test was run against this Open WebUI branch.
2. No live Hermes smoke test was run through the real Open WebUI UI.
3. No end-to-end test was added for the Svelte image-upload gating.
4. No test was added yet for terminal persistence.

## Hermes Source Evidence

Hermes source files that justify the session-continuity fix:

- `/Users/noelbao/Works/hermes-agent/gateway/platforms/api_server.py`
- `/Users/noelbao/Works/hermes-agent/website/docs/user-guide/messaging/open-webui.md`
- `/Users/noelbao/Works/hermes-agent/website/docs/user-guide/features/api-server.md`

Relevant facts from Hermes:

1. Hermes advertises `/v1/models` and `/v1/chat/completions`.
2. The Open WebUI integration docs recommend using `API_SERVER_KEY`.
3. In `gateway/platforms/api_server.py`, session continuation via `X-Hermes-Session-Id` is explicitly guarded so it only works when API-key auth is configured.

The exact server-side guard in Hermes is around:

- `gateway/platforms/api_server.py:639-660`

In plain terms:

- if `X-Hermes-Session-Id` is provided
- and Hermes has no API key configured
- Hermes rejects the request with `403`

That is why the Open WebUI pipe had to stop sending that header by default.

## Manual Smoke-Test Runbook

These steps were not executed yet, but they are the intended next manual checks.

### A. No-auth local Hermes smoke test

Goal:

- verify that the Open WebUI Hermes pipe works without sending `X-Hermes-Session-Id`
- verify there is no ordinary `403` on the no-auth path

Hermes setup:

1. In Hermes, enable the API server on loopback.
2. Do not set `API_SERVER_KEY`.

Likely local startup shape:

```bash
cd /Users/noelbao/Works/hermes-agent
API_SERVER_ENABLED=true hermes gateway
```

Inference note:

- Hermes docs emphasize the authenticated setup, but Hermes code allows loopback operation without a key; this is the path the Open WebUI fix is protecting

Basic API probes:

```bash
curl http://127.0.0.1:8642/v1/models

curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}]}'
```

Open WebUI Hermes pipe settings:

- `hermes_api_url = http://localhost:8642`
- `hermes_api_key = ""`

Expected result:

- ordinary chat works
- no 403 caused by session-header forwarding
- multi-turn continuity relies on request-body history, not Hermes session state

### B. Authenticated Hermes smoke test

Goal:

- verify authenticated session continuity works when both sides are configured

Hermes setup:

```bash
cd /Users/noelbao/Works/hermes-agent
API_SERVER_ENABLED=true API_SERVER_KEY=test-key hermes gateway
```

Basic API probes:

```bash
curl http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer test-key"

curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}]}'
```

Direct session-continuity probe:

```bash
curl http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "X-Hermes-Session-Id: smoke-session-1" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hello"}]}'
```

Open WebUI Hermes pipe settings:

- `hermes_api_url = http://localhost:8642`
- `hermes_api_key = test-key`

Expected result:

- authenticated chats work
- session header is forwarded
- no misleading 403

### C. Image-analysis smoke test

Goal:

- verify non-vision model image uploads work when image analysis is enabled

Expected setup:

1. Enable image analysis in Open WebUI config/admin settings.
2. Select a non-vision chat model.
3. Upload an image through the normal chat input.

Expected result:

- UI does not hard-block
- backend routes to `analyze_image(...)`
- file chip shows processing status
- downstream chat has text extracted/derived from the image

Negative test:

1. Disable image analysis.
2. Select a non-vision model.
3. Upload an image.

Expected result after the remaining frontend fix:

- upload should be blocked or rejected before backend processing

Current state:

- this negative case is not correct yet because the frontend gate is not config-aware

## Remaining Work, In Recommended Order

### 1. Finish the image-analysis P0 slice

Priority:

- `P0`

Files most likely involved:

- `src/lib/components/chat/MessageInput.svelte`
- whichever frontend config/store exposes whether image analysis is enabled
- possibly `src/lib/components/admin/Settings/Documents.svelte` if config shape needs confirmation

Task:

- make the non-vision image-upload allowance conditional on actual image-analysis availability, not just on model capability

Acceptance criteria:

1. non-vision + image analysis enabled -> allowed
2. non-vision + image analysis disabled -> blocked before upload
3. vision-capable models still work as normal

### 2. Restore RAG settings parity

Priority:

- `P1`

Main file:

- `src/lib/components/admin/Settings/Documents.svelte`

Known missing/partial items from review:

- `RAG_EMBEDDING_QUERY_PREFIX`
- `RAG_EMBEDDING_CONTENT_PREFIX`
- `RAG_FULL_DOCUMENT_CONTEXT`
- `RAG_FULL_DOCUMENT_MAX_TOKENS`
- `RAG_SUBCHAT_CONCURRENCY`
- `RAG_DOCUMENT_INDEX_GENERATION`
- `RAG_DOCUMENT_INDEX_MODEL`
- `RAG_DOCUMENT_INDEX_TIMEOUT`
- `RAG_USER_COLLECTION_ENABLED`

Known bug:

- embedding prefixes are declared in UI state but not sent in the update payload

Acceptance criteria:

1. all backend-supported `A`-era settings intended for admin control are visible
2. saves persist
3. reload round-trip works

### 3. Restore agent skill ZIP import in the visible UI

Priority:

- `P1`

Files:

- `src/lib/components/workspace/Skills.svelte`
- `src/lib/apis/skills/index.ts`
- `backend/open_webui/routers/skills.py`

Known state:

- backend route exists
- frontend helper exists
- UI still only accepts `.md,.json`

Acceptance criteria:

1. `.zip` is accepted in the UI
2. upload path calls the ZIP endpoint
3. imported skill is visible and usable

### 4. Fix terminal selection persistence regression

Priority:

- `P1`

Files from earlier review:

- `src/lib/components/chat/Chat.svelte`
- `src/routes/(app)/+layout.svelte`

Known state:

- `Chat.svelte` still expects a persisted `selectedTerminalId`
- layout persistence/restore drifted

Acceptance criteria:

1. terminal selection survives reload/navigation as before
2. no extra regression remains compared with the baseline

### 5. Restore package metadata / lockfile consistency

Priority:

- `P1` or `P2`, depending on release bar

Known state from earlier review:

- `package.json` changed in the port
- matching lockfile parity from `A` was not carried forward cleanly

Task:

- audit `package.json`, `package-lock.json`, and any toolchain changes introduced across the port

### 6. Add broader Hermes integration coverage

Priority:

- `P2`

Needed coverage:

1. more end-to-end confidence around the Hermes Open WebUI integration
2. possibly a higher-level test for pipe/builtin-pipe registration path if that logic changed in `B`
3. manual smoke-test evidence captured in commit/PR notes

## Recommended Working Pattern For The Next Engineer

1. Do not revert unrelated dirty files:
   - `.gitignore`
   - `.webui_secret_key`
   - `backend/open_webui_data.zip`
2. Keep the current Hermes session fix and backend image-analysis dispatch.
3. Finish the image-analysis frontend gate before moving on to `P1` parity items.
4. Re-run the focused backend pytest command after each meaningful backend change.
5. Treat `npm run check` as a noisy global signal, not a precise regression detector.
6. Prefer targeted `rg`/narrowed checks for the files you touch.

## Commands Worth Reusing

Inspect current work:

```bash
git status --short
git diff -- backend/open_webui/pipes/hermes_agent.py \
  backend/open_webui/routers/files.py \
  src/lib/components/chat/MessageInput.svelte \
  backend/open_webui/test/utils/test_hermes_pipe.py \
  backend/open_webui/test/utils/test_file_upload_image_analysis.py \
  docs/hermes-port-remediation-plan.md \
  docs/hermes-port-complete-handoff.md
```

Focused backend tests:

```bash
env PYTHONPATH=backend .venv/bin/pytest \
  backend/open_webui/test/utils/test_hermes_pipe.py \
  backend/open_webui/test/utils/test_file_upload_image_analysis.py
```

Global frontend check:

```bash
npm run check
```

Narrowed grep for edited `MessageInput.svelte` lines:

```bash
npm run check 2>&1 | rg -C 2 "MessageInput\\.svelte:(634|639|640|641|743|744|745|804|1374|1375|1376|1380|1381|1382|1383|1384|1385|1386|1387|1388)"
```

## Final Status Summary

Current truth:

- the Hermes session-continuity bug is fixed in Open WebUI
- backend image-analysis dispatch is restored
- chat upload progress UX is restored in the main path
- focused backend tests exist and pass
- the branch is still not at `A + Hermes = B`

If the next engineer needs a precise next action and wants to maximize correctness per unit time:

1. make the frontend non-vision image gating respect actual image-analysis availability
2. run the manual Hermes smoke tests
3. then proceed to RAG settings parity


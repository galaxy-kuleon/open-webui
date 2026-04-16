# Wave 1 - Reveal Packet

**Wave Objective**: Make non-vision image upload conditional on actual image-analysis config availability — block when disabled, allow when enabled
**Data-flow segment**: entry (upload gating decision)
**Blast radius**: smallest-isolated (single component conditional + config read)
**Total waves**: 1 of 4

## Spec Slice (from remediation plan)

### From Remediation WS2 — Remaining Frontend Gate

Implementation Tasks (remaining):

3. In `src/lib/components/chat/MessageInput.svelte`, change image upload gating:
   - if image analysis is enabled and configured, allow upload even when selected models are non-vision
   - only hard-block when neither vision support nor image analysis support is available
4. Ensure UX messaging matches actual behavior.
5. Confirm analyzed image content lands where downstream RAG / prompt injection expects it.

Acceptance Criteria:

1. With Image Analysis enabled and configured, non-vision model chats can upload images successfully.
2. The backend stores OCR/description output into the file record as intended.
3. Downstream chat behavior uses the analyzed content instead of rejecting the image.
4. With Image Analysis disabled, current vision-only restrictions still behave correctly.

### From Handoff — Important Limitation In The Current P0 Slice

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

## Deferred Items Assigned To This Wave

None

## Constraints for This Wave

- **Allowed files to modify**: `src/lib/components/chat/MessageInput.svelte`, `src/lib/stores/index.ts` (if Config type needs extension), `src/lib/apis/retrieval/index.ts` (if a lightweight config check is needed), `backend/open_webui/routers/retrieval.py` (only if a new lightweight endpoint is needed to expose image analysis status)
- **Forbidden files**: `backend/open_webui/pipes/hermes_agent.py` (already fixed, do not touch), `backend/open_webui/routers/files.py` (backend dispatch already correct, do not touch)
- **Out-of-scope items (deferred to future waves)**: RAG settings UI parity (Wave 3), ZIP import (Wave 2), terminal persistence (Wave 2), lockfile (Wave 4), Hermes coverage (Wave 4)

## Handoff from Wave 0 (Prior Work)

This is the first wave. No prior wave context.

Key prior work already done:
- `backend/open_webui/routers/files.py` correctly dispatches images to `analyze_image(...)` when `IMAGE_ANALYSIS_ENABLED` is true (restored in prior P0 slice)
- `MessageInput.svelte:743-746` currently allows ALL image uploads for non-vision models with an informational toast, regardless of whether image analysis is actually enabled server-side
- Backend tests in `test_file_upload_image_analysis.py` confirm the dispatch logic works (5 tests passing)

## Critical Discovery Context

The principal engineer needs to know:

1. **`IMAGE_ANALYSIS_ENABLED` lives in RAGConfig**, fetched via `getRAGConfig(token)` in `src/lib/apis/retrieval/index.ts`. It is NOT currently exposed in the main `$config` store (`src/lib/stores/index.ts:263` — `Config` type has no image analysis field).

2. **`MessageInput.svelte` uses `$config`** (the main store), not RAGConfig. It does NOT currently import or call `getRAGConfig`.

3. **The gap**: MessageInput.svelte cannot currently check whether image analysis is enabled without either:
   - (a) Adding `IMAGE_ANALYSIS_ENABLED` to the main config endpoint/store, or
   - (b) Making a lightweight retrieval config check from the component, or
   - (c) Exposing a derived feature flag through the existing config surface

4. **Current image gating logic** is at `MessageInput.svelte:743-746`:
   ```js
   if (file['type'].startsWith('image/')) {
       if (visionCapableModels.length === 0) {
           toast.info('Images will be analyzed as text for selected model(s)');
       }
       // ... proceeds to upload regardless
   }
   ```

5. **Vision model detection** uses `visionCapableModels` (line 457-460), populated from model metadata `info.meta.capabilities.vision`.

## Success Criteria

- When Image Analysis is ENABLED in backend config AND selected models are non-vision: image upload is ALLOWED, informational toast shown
- When Image Analysis is DISABLED in backend config AND selected models are non-vision: image upload is BLOCKED with clear error message explaining why
- When selected models ARE vision-capable: image upload always works regardless of image analysis config
- The frontend config check does not introduce a race condition or require an extra network round-trip per upload
- Progress status labels still work correctly for image uploads that proceed through the analysis path
- No regression in the existing vision-model image upload path

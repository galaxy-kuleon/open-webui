# Wave 2 - Reveal Packet

**Wave Objective**: Reconnect agent skill ZIP import in the visible UI + fix terminal selection persistence regression
**Data-flow segment**: entry + transform (file import path + state persistence)
**Blast radius**: small (two isolated UI components, no interaction between them)
**Total waves**: 2 of 4

## Spec Slice (from remediation plan)

### From Remediation WS4 — Agent Skill ZIP Import

Implementation Tasks:

1. In `src/lib/components/workspace/Skills.svelte`, change the file input accept list to include `.zip`.
2. Add the ZIP import branch in the `on:change` handler.
3. Call `uploadSkillZip(...)` for ZIP imports.
4. Refresh skill list state after successful import.
5. Preserve existing JSON and Markdown import behavior.
6. Confirm imported agent skills show the existing `Agent` badge and can be edited/executed as expected.

Acceptance Criteria:

1. Admin or permitted user can import an agent-skill ZIP from the UI.
2. Imported skill appears immediately in the list.
3. Existing JSON/Markdown import paths still work.

### From Remediation WS6 — Terminal Selection Persistence Drift

Problem:

- `src/lib/components/chat/Chat.svelte` still restores terminal enabled state from `selectedTerminalId` (lines 664-672)
- `src/routes/(app)/+layout.svelte` no longer persists/restores `selectedTerminalId`
- Codebase internally inconsistent

Implementation Tasks:

1. Confirm intended `v0.8.12` behavior: persistent selection across reloads OR session-only.
2. If persistence intended: restore `localStorage.selectedTerminalId` sync in layout.
3. If not: remove stale restore logic in `Chat.svelte`.
4. Validate both direct and system terminal paths.

Acceptance Criteria:

1. Terminal selection behavior is explicit and consistent across the codebase.
2. Reload behavior matches intended product behavior.
3. No dead persistence code remains.

## Deferred Items Assigned To This Wave

None

## Constraints for This Wave

- **Allowed files to modify**: `src/lib/components/workspace/Skills.svelte`, `src/lib/components/chat/Chat.svelte`, `src/routes/(app)/+layout.svelte`, `src/lib/stores/index.ts` (if terminal persistence pattern needs store changes)
- **Forbidden files**: `backend/open_webui/pipes/hermes_agent.py`, `backend/open_webui/routers/files.py`, `src/lib/components/chat/MessageInput.svelte` (Wave 1 complete, do not touch)
- **Out-of-scope items (deferred to future waves)**: RAG settings parity (Wave 3), lockfile (Wave 4), Hermes coverage (Wave 4), Google Drive/OneDrive image gate bypass (pre-existing)

## Handoff from Wave 1

Wave 1 closed COMPLETE (3 turns, 0 deferred). The frontend image upload path is now config-aware: non-vision image uploads are blocked when IMAGE_ANALYSIS_ENABLED is false, allowed with an informational toast when true. Vision-model uploads always work. The `imageAnalysisEnabled` store is fail-closed (defaults false), populated once at app init from the RAG config endpoint.

Files modified in Wave 1 (do not re-touch unless fixing a Wave 1 defect):
- `src/lib/stores/index.ts:82-85` (imageAnalysisEnabled store)
- `src/routes/+layout.svelte:960-969` (init fetch)
- `src/lib/components/chat/MessageInput.svelte:744-760,818-824` (gate + shouldProcess)
- `src/lib/i18n/locales/en-US/translation.json` (6 keys)

## Critical Discovery Context

### ZIP Import

1. **Skills.svelte:202**: File input currently accepts `.md,.json` only.
2. **Skills.svelte:204-261**: `on:change` handler has two branches — `ext === 'json'` (lines 209-233) and else/markdown (lines 236-257). No ZIP branch exists.
3. **`uploadSkillZip`** exists at `src/lib/apis/skills/index.ts:2-29` — fully implemented, POSTs FormData to `/skills/upload-zip`.
4. **Backend route** exists at `backend/open_webui/routers/skills.py` (already verified in prior audit).
5. **After import**: need to call `loadSkillItems()` + `_skills.set(await getSkills(localStorage.token))` like the JSON path does at lines 228-229.

### Terminal Persistence

1. **Store**: `selectedTerminalId` at `src/lib/stores/index.ts:111` — `Writable<string | null> = writable(null)`, no localStorage binding.
2. **Chat.svelte:664-672**: Restores terminal `enabled` state from `$selectedTerminalId` on mount. Expects it to hold a persisted value from a prior session.
3. **Layout**: `src/routes/(app)/+layout.svelte` has ZERO references to `selectedTerminalId`. No persistence, no restore. The store always starts as `null` on page load.
4. **TerminalMenu.svelte:42-43,63**: Writes to the store via `selectedTerminalId.set(...)` — this works within a session but is lost on reload.
5. **The inconsistency**: Chat.svelte expects persisted state that layout never provides. The restore logic at 664-672 is dead code (always reads `null`).
6. **Other stores' persistence pattern**: Check how other similar stores (e.g., `selectedModelId`) handle localStorage persistence for the right pattern.

## Success Criteria

- `.zip` files are accepted by the Skills file input
- ZIP imports call `uploadSkillZip(...)` and refresh the skill list on success
- Existing `.md` and `.json` import paths are not broken
- Error handling for invalid ZIPs shows a clear toast
- Terminal selection either persists across reload (with working restore) or the dead restore code is removed
- No inconsistency remains between Chat.svelte's expectations and layout's behavior
- Both direct and system terminal paths work correctly

# Wave 3 - Reveal Packet

**Wave Objective**: Restore RAG admin settings parity with A — all backend-supported retrieval settings must be visible, editable, and persist in the Documents settings UI
**Data-flow segment**: transform (admin config round-trip: UI → API → persistence → reload)
**Blast radius**: moderate (admin settings page + API payloads + 9 config values)
**Total waves**: 3 of 4

## Spec Slice (from remediation plan)

### From Remediation WS3 — Admin RAG Settings Parity

Implementation Tasks:

1. In `Documents.svelte`, include embedding prefixes in the payload sent by `updateEmbeddingConfig(...)`.
2. Restore the missing admin controls for: Full Document Context, Max Document Tokens, Sub-Chat Concurrency, Document Index Generation, Index Generation Model, Index Generation Timeout, User Collection.
3. Verify values are loaded from backend config into UI state on page load.
4. Verify save operations update both in-memory app state and persistent config storage.
5. Audit any other A-era retrieval settings now present in backend but not surfaced in the page.

Acceptance Criteria:

1. Every backend retrieval/config feature ported from A and intended for admin control is visible and editable in the Documents settings UI.
2. Saving the page actually updates those backend values.
3. Reloading the page preserves the saved values.

## Deferred Items Assigned To This Wave

None

## Constraints for This Wave

- **Allowed files to modify**: `src/lib/components/admin/Settings/Documents.svelte`, `src/lib/i18n/locales/en-US/translation.json` (for new UI labels)
- **Forbidden files**: `backend/open_webui/routers/retrieval.py` (backend already complete), `hermes_agent.py`, `files.py`, `MessageInput.svelte`, `Skills.svelte`, `Chat.svelte`
- **Out-of-scope items**: Lockfile (Wave 4), Hermes coverage (Wave 4)

## Handoff from Wave 2

Wave 2 closed COMPLETE (3 turns, 0 deferred). ZIP skill import wired. Dead terminal restore code removed. No interaction with Wave 3's scope.

## Critical Discovery Context

### Two Separate Config Pipelines

Documents.svelte has TWO distinct config save paths:

1. **Embedding config** (`updateEmbeddingConfig` at line 115): Saves embedding engine, model, batch size, async, concurrency, and provider configs (ollama/openai/azure). **Does NOT include embedding prefixes.**

2. **RAG config** (`updateRAGConfig` at line 224 via `submitHandler`): Spreads `...RAGConfig`. Since `RAGConfig = config` at line 284 (loaded from `getRAGConfig`), ALL fields returned by the backend are round-tripped. **But no UI controls exist for 7 settings.**

### Embedding Prefix Bug (pipeline 1)

- `RAG_EMBEDDING_QUERY_PREFIX` and `RAG_EMBEDDING_CONTENT_PREFIX` are declared as local vars at `Documents.svelte:46-47`
- Backend `getEmbeddingConfig` (retrieval.py:284-285) returns them in the response
- Frontend `setEmbeddingConfig()` (Documents.svelte:247-267) does NOT extract them from the response
- Frontend `updateEmbeddingConfig()` (Documents.svelte:115-133) does NOT include them in the payload
- Backend update handler (retrieval.py:328-329, 361-364) accepts and persists them
- **Fix needed**: Load the prefixes in `setEmbeddingConfig`, include them in `updateEmbeddingConfig` payload
- Also: `RAG_EMBEDDING_PREFIX_FIELD_NAME` (retrieval.py:286) may need the same treatment — check if it exists in UI

### Missing RAG Config UI Controls (pipeline 2)

These are all returned by `getRAGConfig` and stored in `RAGConfig`, and sent back via `...RAGConfig` spread. They load/save correctly but are invisible to the admin:

| Field                           | Type | Backend location     | UI status     |
| ------------------------------- | ---- | -------------------- | ------------- |
| `RAG_FULL_DOCUMENT_CONTEXT`     | bool | retrieval.py:478,817 | No UI control |
| `RAG_FULL_DOCUMENT_MAX_TOKENS`  | int  | retrieval.py:479,822 | No UI control |
| `RAG_SUBCHAT_CONCURRENCY`       | int  | retrieval.py:480,827 | No UI control |
| `RAG_DOCUMENT_INDEX_GENERATION` | bool | retrieval.py:481,832 | No UI control |
| `RAG_DOCUMENT_INDEX_MODEL`      | str  | retrieval.py:482,837 | No UI control |
| `RAG_DOCUMENT_INDEX_TIMEOUT`    | int  | retrieval.py:483,842 | No UI control |
| `RAG_USER_COLLECTION_ENABLED`   | bool | retrieval.py:488,867 | No UI control |

### File Structure

`Documents.svelte` is 1720 lines. The template contains these sections (approximate):

- Embedding engine/model config (~lines 340-700)
- RAG Template (~700-780)
- Top K, Hybrid Search (~780-830)
- Image Analysis (~830-890)
- Content Extraction (~890-1200)
- File settings (~1200-1400)
- Action buttons (~1400-1720)

New controls should be placed in logical sections near related settings.

## Success Criteria

- Embedding prefixes load from backend config on page mount
- Embedding prefixes are sent in the `updateEmbeddingConfig` payload on save
- All 7 RAG config settings have visible, editable UI controls
- All 7 controls load correct values from `RAGConfig` on mount (already works via spread)
- Saving the page persists all values (already works for RAG config via spread, needs fixing for embedding prefixes)
- Reloading the page shows saved values
- New i18n keys added for all new UI labels
- Existing Documents settings page behavior is not regressed

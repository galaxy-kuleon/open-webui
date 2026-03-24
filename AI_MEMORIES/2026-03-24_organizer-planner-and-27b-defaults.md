# Organizer Planner Refactor and 27B Default Models

## Date: 2026-03-24

**Status:** COMPLETED

## What Changed

- Changed backend default RAG text-model configs from `lmstudio.unsloth/qwen3.5-35b-a3b` to `lmstudio.unsloth/qwen3.5-27b` in `backend/open_webui/config.py` for:
  - `RAG_DOCUMENT_INDEX_MODEL`
  - `RAG_RESEARCH_MODEL`
  - `RAG_KNOWLEDGE_ORGANIZER_MODEL`
- Refactored `backend/open_webui/utils/knowledge_export.py` so the organizer no longer lets OpenCode move files directly.
- New organizer flow:
  1. Backend collects inbox documents and previews
  2. OpenCode is asked for a JSON destination plan only
  3. OpenCode tool permissions are denied for the planner run (`{"*": "deny"}`)
  4. Backend validates destination paths and moves the exact `.md` / `.index.md` files in Python
  5. Backend rebuilds `_catalog.md`
- Planner requests are batched (`12` docs per run) so a backed-up inbox does not create one giant prompt.
- Added OpenCode JSON error extraction so LM Studio / provider failures surface clearly instead of looking like "no planner text".
- Added support for OpenCode `result` events in planner text extraction.

## Why

- Weak tool-use models were mutating CJK filenames and relying on shell `cd` state that does not persist across tool calls.
- Moving exact files in Python removes the model from the brittle part of the workflow.
- Denying tools for the organizer planner keeps the model focused on classification instead of filesystem operations.

## Verification

- `uv run --no-sync python -m py_compile backend/open_webui/config.py backend/open_webui/utils/knowledge_export.py`
- Functional temp-dir organizer test passed with `lmstudio.unsloth/qwen3.5-35b-a3b`:
  - inbox became empty
  - `.md` and `.index.md` moved together
  - `_catalog.md` regenerated
- Live retrieval config updated through `/api/v1/retrieval/config/update` to `lmstudio.unsloth/qwen3.5-27b` for document index / research / organizer
- Restarted the live server on port `28080` via `SIGTERM`, then relaunched it and verified `/api/version`
- Verified post-restart live retrieval config returns `lmstudio.unsloth/qwen3.5-27b` for all three model settings

## Important Caveat

- `lms ls` confirms `unsloth/qwen3.5-27b` exists locally, but an OpenCode load attempt on this machine hit an LM Studio guardrail: model load stopped due to insufficient system resources (~95.58 GB required under current settings).
- Result: fresh installs using the new default `27b` model may fail for document indexing / research / organizer until LM Studio can actually load that model (guardrail/settings/memory situation).

## Expansion Log

- Loaded memories: `AI_MEMORIES/environment.md`, `AI_MEMORIES/2026-03-24_0020_opencode-qwen-organizer-failure.md`, `AI_MEMORIES/errors-and-lessons.md`
- Inspected code: `backend/open_webui/config.py`, `backend/open_webui/utils/knowledge_export.py`, `backend/open_webui/utils/opencode.py`
- Verified model availability with `lms ls`
- Verified Open WebUI authenticated model list on port `28080`
- Ran temp-dir organizer integration tests via `uv run --no-sync python`
- Live restart detail: preserving the user's JWT required launching with `WEBUI_SECRET_KEY` from `knowledge-base/.webui_secret_key`; starting from repo root without that env var loaded the wrong secret file and caused `401 Unauthorized`

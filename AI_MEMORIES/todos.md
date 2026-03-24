# TODOs / Incomplete Work

**Last updated:** 2026-03-24

## Active

### P0: Verify LM Studio Can Actually Load `unsloth/qwen3.5-27b`

- **What:** Backend defaults now point `RAG_DOCUMENT_INDEX_MODEL`, `RAG_RESEARCH_MODEL`, and `RAG_KNOWLEDGE_ORGANIZER_MODEL` at `lmstudio.unsloth/qwen3.5-27b`
- **Added:** 2026-03-24
- **Risk:** `lms ls` lists the model, but an OpenCode planner run failed because LM Studio memory guardrails blocked loading (~95.58 GB required under current settings)
- **Next step:** Either adjust LM Studio guardrails / memory settings so `27b` can load, or choose a smaller default if startup behavior matters more than model preference
- **User direction:** Keep `27b` anyway because the real LM Studio host is a larger remote Mac Studio

### P0: Harden Knowledge Organizer Against Filename Drift and Infinite Retry Loops

- **What:** The OpenCode organizer running on `lmstudio/unsloth/qwen3.5-35b-a3b` mutates exact CJK filenames and can loop forever on failed `mv` commands.
- **Added:** 2026-03-24
- **Evidence:** `server.log` shows `read failed` and repeated `mv: No such file or directory` from lines 852-1034 while the organizer process remains alive.
- **Status:** Implemented on 2026-03-24 via planner-only OpenCode flow + Python exact moves
- **Remaining follow-up:** none for deployment; live server was restarted after the patch and is now serving on port `28080`

### P0: Investigate KG1 / OCR Timeout Failures During Document Indexing

- **What:** Post-restart server observation shows the organizer succeeding, but large-PDF processing repeatedly times out against the OCR backend on `127.0.0.1:11434`
- **Added:** 2026-03-24
- **Evidence:** `server.log:2132` onward shows repeated `OCR API request error`; `server.log:2168` shows document index generation failing with `TimeoutError`
- **Update 2026-03-24:** the user-reported `Document index generation failed ... TimeoutError` was hardened separately in `backend/open_webui/routers/retrieval.py` via smaller initial index chunks (`48k/8k`), adaptive retry splitting, and timeout cancellation.
- **Update 2026-03-24 23:10:** `/tmp/owui-test.log` plus `glm-ocr-latest-test` source confirm the dominant remaining issue is glm-ocr self-hosted OCR fan-out (`max_workers: 32`) with `300s` request timeouts / retries against local Ollama.
- **Remaining next step:** patch either glm-ocr or the KG1 integration so Open WebUI can lower self-hosted `pipeline.max_workers` and raise/tune self-hosted `pipeline.ocr_api.request_timeout`

### P0: Commit All Unstaged Changes

- **What:** ALL feature work from 2026-03-21 through 2026-03-23 is NOT committed. 30+ files changed across backend and frontend.
- **Added:** 2026-03-22, **updated:** 2026-03-23 (scope grew -- idle timeout, subprocess cleanup, Ollama notification added)
- **Staged (in index):** 12 files (config.py, main.py, kg1.py, loaders/main.py, retrieval/utils.py, routers/retrieval.py, knowledge_export.py, middleware.py, research.py, .gitignore, data/readme.txt, Documents.svelte)
- **Unstaged only:** 18+ files (routers/files.py, tools/builtin.py, package-lock.json, apis/files/index.ts, Chat.svelte, MessageInput.svelte, AgentSkillStatus.svelte, StatusItem.svelte, FileItem.svelte, +layout.svelte, opencode.py, and others)
- **Risk:** If branch is switched or reset, ALL work is lost
- **Next step:** Stage all relevant files and commit. Consider splitting into logical commits (KG1+RAG, Agent Skills, User Collection, Idle Timeout + Cleanup)

### P0: User Collection Feature -- Real-World Testing

- **What:** `RAG_USER_COLLECTION_ENABLED` is implemented but not tested end-to-end with real uploads
- **Added:** 2026-03-22
- **Test plan:** Enable toggle in Admin > Documents, upload files in Chat A, verify they are searchable from Chat B with no file attachment
- **Verify:** Cross-chat search works, file deletion cleans up user collection, re-process doesn't double-add

### P0: /research Command End-to-End Testing

- **What:** `/research <query>` command not yet tested end-to-end in chat
- **Where:** `backend/open_webui/utils/research.py`
- **Added:** 2026-03-22
- **Depends on:** RAG_KNOWLEDGE_EXPORT_ENABLED + RAG_KNOWLEDGE_EXPORT_DIR must be set, OpenCode installed

### P1: ChromaDB Orphaned Collections Cleanup

- **What:** 2 orphaned collections found in ChromaDB vector_db (`file-4ba8b0d2...`, `file-e6958d08...`) with no corresponding file records
- **Added:** 2026-03-23
- **ChromaDB size:** 143 MB in `data/vector_db/` (14 collections total)
- **Fix options:** Direct ChromaDB API intervention required (not cleanable via WebUI)
- **Impact:** Wasted storage, minor

### P1: OpenCode .index.md File Pairing Issue

- **What:** OpenCode organizer sometimes doesn't move `.index.md` files along with their paired `.md` files when reorganizing directories
- **Where:** Knowledge export / organization pipeline
- **Added:** 2026-03-22
- **Updated:** 2026-03-24 (confirmed Qwen organizer run can fail before moving either pair because filename/path handling drifts)
- **Impact:** Index content gets separated from its source document in the organized knowledge base
- **Possible fix:** Update SKILL.md instructions to explicitly mention pairing, or post-process to re-pair

### P1: Fix ddgs Version Pin in pyproject.toml

- **What:** `ddgs==9.11.2` yanked from PyPI, blocks `uv sync`
- **Where:** `pyproject.toml`
- **Added:** 2026-03-22
- **Fix:** Change to `ddgs>=9.11,<10` or `ddgs==9.11.4`
- **Note:** This is an upstream issue (Open WebUI mainline)

### P1: Frontend Progress Text Only Shows in Non-Small FileItem Mode

- **What:** Processing status text (extracting/embedding/indexing) only visible when FileItem is in default (non-small) mode
- **Where:** `src/lib/components/common/FileItem.svelte`
- **Added:** 2026-03-22
- **Impact:** Users in compact/small file views don't see processing progress

### P1: Automated Tests for New Features

- **What:** No tests written for KG1Loader, Full Document Context, Sub-Chat extraction, Document Index Generation, Knowledge Export, User Collection, Idle Timeout, Subprocess Cleanup
- **Added:** 2026-03-22, **updated:** 2026-03-23
- **Scope:** Unit tests for utility functions, integration tests for pipeline

### P2: Merge Back to Main / Rebase

- **What:** Branch `feat/opencode-agent-skill-integration` has diverged significantly from `main`
- **Added:** 2026-03-22
- **Note:** Multiple new files and substantial modifications; will need careful conflict resolution

### P2: Install ffmpeg for pydub

- **What:** `pydub` warns about missing `ffmpeg` at import time
- **Where:** System dependency
- **Added:** 2026-03-22
- **Fix:** `brew install ffmpeg`

### P2: User Collection Per-Chat Opt-Out

- **What:** Currently user collection search is always-on when globally enabled. Consider per-chat toggle to disable cross-chat RAG for specific conversations.
- **Added:** 2026-03-22
- **Rationale:** Some chats may be for unrelated topics where cross-chat documents add noise

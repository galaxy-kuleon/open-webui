# Goals

## Original Goals (North Star)

### OG-1: Enhanced Document Processing for Open WebUI [ACTIVE]

- **What:** Replace Open WebUI's default document extraction with a high-quality OCR-based pipeline that handles PDFs, images, and Office documents with full layout preservation
- **When:** 2026-03-21
- **Why:** Default extractors lose layout, tables, formulas; CJK support is poor

### OG-2: Intelligent RAG with Full Document Context [ACTIVE]

- **What:** Move beyond naive chunk retrieval to full-document-aware RAG with AI-generated indexes and sub-chat extraction
- **When:** 2026-03-22
- **Why:** Chunk-only retrieval loses cross-section context; answers are fragmented

### OG-3: OpenCode Agent Skills Integration [ACTIVE]

- **What:** Allow OpenCode agent skills (SKILL.md + tooling) to run inside Open WebUI as a new skill type
- **When:** 2026-03-21
- **Why:** Enables autonomous agent workflows within the chat UI

### OG-4: Knowledge Export + AI Organization [ACTIVE]

- **What:** Export processed documents to filesystem, use OpenCode agent to organize into semantic directory structures
- **When:** 2026-03-22
- **Why:** Enables file-based knowledge base that agents can browse, search, and reason over

## Emergent Goals (Arose During Work)

### EG-1: Document Index Generation [ACTIVE]

- **What:** AI generates structured indexes (entities, events, temporal info, relationships) per document, embedded alongside content chunks
- **When:** 2026-03-22
- **Why:** Emerged from realizing that chunk search alone misses document-level patterns; indexes improve retrieval relevance

### EG-2: /research Command [ACTIVE]

- **What:** Chat command that spawns OpenCode to research the knowledge base and generate reports
- **When:** 2026-03-22
- **Why:** Natural extension of knowledge export -- once files are on disk, agents can analyze them

### EG-3: Processing Progress UX [ACTIVE]

- **What:** Granular status updates during document processing (extracting, embedding, indexing)
- **When:** 2026-03-22
- **Why:** Users had no visibility into multi-step processing pipeline; felt like a black box

### EG-4: Qwen3 Embedding Configuration [COMPLETED]

- **What:** PersistentConfig upgrade for embedding prefixes + auto-fill for Qwen3 models
- **When:** 2026-03-22
- **Why:** Qwen3 embeddings require instruction prefixes; env-only config was inconvenient
- **Completed:** 2026-03-22

### EG-5: User Collection / Per-User Cross-Chat RAG [COMPLETED]

- **What:** `RAG_USER_COLLECTION_ENABLED` -- every uploaded file auto-indexed into `user-{user_id}` collection, enabling cross-chat RAG search with zero user action
- **When:** 2026-03-22
- **Why:** Users upload files in one chat but need to reference them in another chat. Without this, each chat's RAG scope is isolated to its own attached files. User collection breaks this silo.
- **Completed:** 2026-03-22
- **Files:** config.py, main.py, routers/retrieval.py, routers/files.py, tools/builtin.py, utils/middleware.py, Documents.svelte
- **Status note:** Code complete, format/lint/check passed, NOT committed (all changes unstaged as of end-of-session 2026-03-22)

### EG-6: Idle-Based Subprocess Timeout [COMPLETED]

- **What:** Replaced hard `subprocess.run(timeout=N)` with idle-based timeout -- only kills subprocess if no output for 600s
- **When:** 2026-03-23
- **Why:** Hard 120s timeout was killing OpenCode processes that were actively producing output but taking longer than the limit. Agent tasks like research and knowledge organization are legitimately long-running.
- **Completed:** 2026-03-23
- **Files:** opencode.py, knowledge_export.py, research.py
- **Pattern:** asyncio readline + idle check (opencode.py), Popen + drain threads + idle poll (knowledge_export.py, research.py)

### EG-7: Subprocess Cleanup on Server Shutdown [COMPLETED]

- **What:** Track all spawned subprocesses and kill them on server shutdown via lifespan + atexit dual strategy
- **When:** 2026-03-23
- **Why:** Orphaned OpenCode processes were persisting after server restarts, consuming resources with no parent
- **Completed:** 2026-03-23
- **Files:** opencode.py, knowledge_export.py, research.py, main.py

### EG-8: Ollama Connection Notification [COMPLETED]

- **What:** Frontend toast warning when Ollama is unreachable at app load
- **When:** 2026-03-23
- **Why:** Users had no way to know if Ollama was down -- models would just silently fail
- **Completed:** 2026-03-23
- **File:** `src/routes/(app)/+layout.svelte`

## Abandoned / Adjusted Goals + Reasons

### AG-1: DeepSeek-OCR-2 as Primary OCR Engine [ABANDONED]

- **What:** Use DeepSeek-OCR-2 directly for document extraction
- **When abandoned:** 2026-03-21
- **Why abandoned:** DeepSeek-OCR-2 requires CUDA/GPU with Flash Attention 2; macOS MPS path is slow (~1-2 min/image) and requires patches. MLX path exists but is a separate ecosystem. Chose glm-ocr via Ollama instead -- already runs locally, supports MPS via Ollama's Metal backend, simpler integration.
- **What replaced it:** KG1 engine using glm-ocr:bf16 model via Ollama

### AG-2: ocrmac as OCR Backend [ABANDONED]

- **What:** Use Apple's native Vision/LiveText framework via ocrmac Python library
- **When abandoned:** 2026-03-21
- **Why abandoned:** ocrmac produces raw text without layout/structure preservation. No table detection, no formula handling, no Markdown output. LiveText is fast but too simple for document-level OCR. DeepSeek/glm-ocr produces structured Markdown with tables, headers, formulas.
- **What replaced it:** KG1 engine using glm-ocr

### AG-3: Direct `uv run` for subprocess calls [ADJUSTED]

- **What:** Use `uv run` directly to invoke glm-ocr
- **When adjusted:** 2026-03-21
- **Why adjusted:** `uv run` inherits parent process env vars (VIRTUAL_ENV, PYTHONPATH, etc.) which causes it to find the wrong Python environment. Must explicitly clean these env vars before subprocess call.
- **Adjustment:** Clean 6 specific env vars before subprocess execution

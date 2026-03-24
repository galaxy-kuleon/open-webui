# Architecture Decisions & Patterns

**Last updated:** 2026-03-22

## System Overview

Custom fork of Open WebUI with enhanced RAG pipeline, KG1 content extraction, OpenCode agent skills, and knowledge export system.

```
User Upload -> KG1Loader (glm-ocr via Ollama) -> Markdown
                                                    |
                                          +---------+---------+
                                          |                   |
                                    Text Splitter      Index Generator
                                    (token/markdown)   (AI-generated)
                                          |                   |
                                          +----> ChromaDB <---+
                                          |         |         |
                                   Per-chat    User Collection   Knowledge Export
                                   collection  (user-{user_id})  (filesystem + OpenCode)
                                          |         |                   |
                              +-----------+---------+            /research command
                              |
                     Chunk Retrieval (per-chat + user collection)
                              |
                     Full Document Context
                              |
                     Sub-Chat Extraction
                     (if > token budget)
                              |
                     Chat Response
```

## Key Architecture Decisions

### AD-1: KG1 Uses Subprocess, Not Library Import

- **Decision:** glm-ocr is invoked via `subprocess.run(["uv", "run", "glmocr", "parse", ...])` not as a Python library
- **Why:** glm-ocr has its own heavy dependencies (PyTorch, transformers, etc.) that conflict with Open WebUI's deps. Subprocess isolation prevents version conflicts.
- **Trade-off:** Slower (process spawn overhead), but fully isolated

### AD-2: Synchronous KG1Loader with threading.Semaphore

- **Decision:** KG1Loader.load() is synchronous, uses `threading.Semaphore` for concurrency control
- **Why:** Open WebUI's loader interface (`_get_loader()`) returns sync objects. The calling code runs loaders in sync context.
- **Config:** `KG1_GLM_OCR_CONCURRENCY` controls max concurrent OCR processes

### AD-3: Full Document Context via file_id Tracing

- **Decision:** After chunk retrieval, trace chunks back to source files via `metadata.file_id`, then load full `.md` content from Files table
- **Why:** Chunk-only context loses cross-section information. Full documents give the LLM complete picture.
- **Pattern:** `expand_sources_to_full_documents()` in middleware.py

### AD-4: Sub-Chat Map-Reduce for Token Budget

- **Decision:** When full documents exceed `RAG_FULL_DOCUMENT_MAX_TOKENS`, spawn per-document sub-completions to extract relevant info
- **Why:** Can't fit unlimited documents in context. Sub-chat extracts only query-relevant portions.
- **Pattern:** `asyncio.Semaphore` controls concurrency; `generate_chat_completion(bypass_filter=True)` prevents recursive RAG

### AD-5: Document Index as Separate Vector DB Entries

- **Decision:** AI-generated indexes are stored in the same ChromaDB collection as content chunks, distinguished by `metadata.type="index"`
- **Why:** Indexes improve retrieval by providing entity/relationship/temporal search surfaces. Same collection = same search query hits both.

### AD-6: Knowledge Export to Filesystem

- **Decision:** Processed `.md` and `.index.md` files are exported to `RAG_KNOWLEDGE_EXPORT_DIR` with YAML frontmatter
- **Why:** Enables file-based workflows -- OpenCode agents, grep, git, external tools can all work with files
- **Pattern:** Background job queue (`threading.Queue` + daemon worker) for OpenCode organization

### AD-7: OpenCode Agent Skills as Sandboxed Execution

- **Decision:** Agent skills run in copied sandbox directories with full permissions, NOT in the main project
- **Why:** Safety -- skills can read/write/execute but only within their sandbox. `opencode run` auto-approves in non-interactive mode.
- **Config:** Concurrent limit 2 per user via `asyncio.Semaphore`, 300s timeout

### AD-8: Idle-Based Subprocess Timeout (Not Hard Timeout)

- **Decision:** All OpenCode/organizer/research subprocesses use idle-based timeout (kill only if no output for N seconds) instead of hard wall-clock timeout
- **Why:** Agent tasks like research and knowledge organization are legitimately long-running (>120s). Hard timeouts kill active processes. Idle timeout only kills truly stuck ones.
- **Pattern:**
  - `opencode.py`: asyncio readline loop with `DEFAULT_IDLE_TIMEOUT = 600`, checks elapsed since last output
  - `knowledge_export.py` / `research.py`: `subprocess.Popen` with drain threads (one per stdout/stderr), shared `last_activity` timestamp, main thread polls for idle
- **Trade-off:** Cannot use `subprocess.run(timeout=)` anymore; more complex code but much more robust

### AD-9: Dual Shutdown Cleanup (Lifespan + atexit)

- **Decision:** Subprocess cleanup runs in BOTH FastAPI lifespan shutdown AND `atexit` handler
- **Why:** SIGTERM to uvicorn does not reliably trigger lifespan shutdown. `atexit` catches the gap.
- **Where:** `main.py` -- `_kill_all_subprocesses()` registered in both lifespan yield and `atexit.register()`
- **Pattern:** Every module with subprocesses exports `kill_all_*()` function; main.py calls all of them

### AD-10: Lazy Imports for Circular Dependency Breaking

- **Decision:** Heavy utility functions (especially `generate_chat_completion`) are imported inside function bodies, not at module level
- **Why:** Open WebUI has complex import chains; module-level imports cause circular dependency errors
- **Where:** `routers/retrieval.py` imports from `utils/chat.py` lazily

### AD-11: User Collection for Cross-Chat RAG

- **Decision:** Every uploaded file is also indexed into a per-user vector collection (`user-{user_id}`) alongside the normal per-chat/knowledge collection. Middleware injects this collection into RAG search even when no files are attached to the current chat.
- **Why:** Users upload documents across many chats but expect the AI to "know" all their documents. Without user collection, each chat is an isolated RAG silo. User collection breaks this silo with zero extra user action.
- **Pattern:** `add=True` (append mode) for embedding; `file_id` filter for delete/re-process; dedup check prevents double-adding; middleware enters file processing block even with empty files list when feature is enabled
- **Trade-off:** Increases total vector DB storage (every file is stored in 2 collections), but enables fundamentally better cross-chat experience

## Concurrency Model

| Component             | Type  | Mechanism                            | Config                             | Timeout            |
| --------------------- | ----- | ------------------------------------ | ---------------------------------- | ------------------ |
| KG1 OCR               | Sync  | `threading.Semaphore`                | `KG1_GLM_OCR_CONCURRENCY`          | --                 |
| Sub-chat extraction   | Async | `asyncio.Semaphore`                  | `RAG_SUBCHAT_CONCURRENCY`          | --                 |
| Agent skill execution | Async | `asyncio.Semaphore`                  | 2 per user                         | 600s idle          |
| Knowledge export      | Sync  | `threading.Queue` + daemon           | Single worker                      | 600s idle (Popen)  |
| /research command     | Sync  | Popen + drain threads                | --                                 | 600s idle (Popen)  |
| Sync-to-async bridge  | --    | `asyncio.run_coroutine_threadsafe()` | Uses `request.app.state.main_loop` | --                 |
| Shutdown cleanup      | --    | lifespan + `atexit` dual             | `_kill_all_subprocesses()` in main | --                 |

## File Organization

### New Backend Files (This Fork)

- `backend/open_webui/retrieval/loaders/kg1.py` -- KG1 content extraction
- `backend/open_webui/utils/knowledge_export.py` -- Knowledge export + OpenCode organization
- `backend/open_webui/utils/research.py` -- /research chat command
- `backend/open_webui/utils/opencode.py` -- OpenCode agent skill sandbox execution

### Key Modified Files

- `config.py` -- All new PersistentConfig variables (incl. RAG_USER_COLLECTION_ENABLED)
- `main.py` -- App state initialization for new configs
- `routers/retrieval.py` -- Config form extensions, KG1 routing, index generation, user collection embedding
- `routers/files.py` -- User collection cleanup on file deletion
- `tools/builtin.py` -- query_knowledge_files includes user collection
- `utils/middleware.py` -- Full document context, sub-chat extraction, progress events, user collection injection
- `retrieval/utils.py` -- PersistentConfig `.value` access for embedding prefixes
- `retrieval/loaders/main.py` -- KG1 routing in `_get_loader()`
- `Documents.svelte` -- Admin UI for all new features

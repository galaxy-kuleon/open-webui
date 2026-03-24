# Idle-Based Timeout, Ollama Notification, Subprocess Cleanup — Session Log

## Date: 2026-03-23

**Status:** COMPLETED

## What Was Done

### 1. OpenCode Idle-Based Timeout (replaced 120s hard timeout with 600s idle timeout)

- **Problem:** Hard `subprocess.run(timeout=N)` killed long-running OpenCode processes even when actively producing output
- **Solution:** Switched to idle-based timeout: only kills if no stdout/stderr output for 10 minutes (600s)
- **Files modified:**
  - `backend/open_webui/utils/opencode.py` -- `DEFAULT_IDLE_TIMEOUT = 600`, uses asyncio readline with idle check loop
  - `backend/open_webui/utils/knowledge_export.py` -- `subprocess.run` -> `subprocess.Popen` with drain threads + idle poll
  - `backend/open_webui/utils/research.py` -- same Popen + drain threads + idle poll pattern

### 2. Ollama Connection Notification

- **Problem:** Users had no indication when Ollama was unreachable
- **Solution:** Frontend toast warning on app load when Ollama is down
- **File:** `src/routes/(app)/+layout.svelte` -- added `getOllamaVersion` check after models load
- **UX:** "Ollama is not reachable. Make sure Ollama is running at the configured URL." (8s duration)

### 3. Subprocess Cleanup on Server Shutdown

- **Problem:** OpenCode subprocesses were orphaned when server restarted without cleanup hooks
- **Solution:** All three subprocess-spawning files track active processes in `_active_processes` sets with thread-safe locks
  - `opencode.py` -- `_active_processes: set[asyncio.subprocess.Process]`, `kill_all_opencode_processes()`
  - `knowledge_export.py` -- `_active_processes: set[subprocess.Popen]`, `kill_all_organizer_processes()`
  - `research.py` -- `_active_processes: set[subprocess.Popen]`, `kill_all_research_processes()`
- **Shutdown hooks:** `main.py` lifespan shutdown calls all three `kill_all_*()` functions via `_kill_all_subprocesses()`
- **Fallback:** `atexit.register(_kill_all_subprocesses)` in main.py handles cases where lifespan shutdown doesn't fire (e.g., SIGTERM to uvicorn)

### 4. Data Cleanup Research (planned, not implemented)

- **File uploads:** 19 files, 2.9 MB in `data/uploads/`
- **ChromaDB:** 14 collections (2 orphaned), 143 MB in `data/vector_db/`
- **Knowledge base:** 6.5 MB in `knowledge-base/`
- **Orphaned collections:** `file-4ba8b0d2...`, `file-e6958d08...`
- **Cleanup options:**
  - WebUI Admin > Documents > Delete All (for managed files)
  - Individual file deletion via UI
  - Orphaned collections require direct ChromaDB API intervention

## Key Lessons Learned

1. **SIGTERM to uvicorn doesn't always trigger FastAPI lifespan shutdown** -- need `atexit` as fallback
2. **`select.select()` doesn't work with `subprocess.PIPE` in Python text mode** -- must use thread-based drain pattern (separate threads reading stdout/stderr, updating a shared `last_activity` timestamp)
3. **Orphaned processes accumulate silently** -- always pair subprocess creation with a cleanup mechanism (tracking set + shutdown hook)

## Status: COMPLETED

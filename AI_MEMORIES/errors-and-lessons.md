# Errors and Lessons Learned

## CRITICAL: Environment Variable Contamination in Subprocess Calls

- **When:** 2026-03-21
- **What:** `uv run glmocr parse` in KG1Loader failed because parent Open WebUI process sets VIRTUAL_ENV, CONDA_PREFIX, PYTHONHOME, PYTHONPATH, UV_PROJECT_ENVIRONMENT
- **Symptom:** uv finds wrong Python version/environment, subprocess crashes with import errors
- **Fix:** Clean these 6 env vars before subprocess: `VIRTUAL_ENV`, `CONDA_PREFIX`, `CONDA_DEFAULT_ENV`, `PYTHONHOME`, `PYTHONPATH`, `UV_PROJECT_ENVIRONMENT`
- **Where:** `backend/open_webui/retrieval/loaders/kg1.py`
- **How to avoid:** Always sanitize env when spawning subprocess that uses its own venv/uv project

## CRITICAL: glm-ocr `--layout-device` CLI Flag Does NOT Accept "mps"

- **When:** 2026-03-21
- **What:** CLI argument `--layout-device mps` silently fails or errors
- **Symptom:** Layout detection doesn't work, empty or degraded OCR results
- **Fix:** Use `GLMOCR_LAYOUT_DEVICE=mps` environment variable instead of CLI flag
- **Where:** `backend/open_webui/retrieval/loaders/kg1.py`

## CRITICAL: glm-ocr Requires opencv-python for Layout Detection

- **When:** 2026-03-21
- **What:** glm-ocr venv missing `opencv-python` package
- **Symptom:** Layout detection returns empty results silently (no error, just empty output)
- **Fix:** Install `opencv-python` in glm-ocr's own venv
- **Where:** glm-ocr project at `/Users/noelbao/Works/glm-ocr-latest-test/`

## Circular Import: generate_chat_completion in retrieval.py

- **When:** 2026-03-22
- **What:** Importing `generate_chat_completion` at module level in `routers/retrieval.py` causes circular import
- **Symptom:** ImportError at startup
- **Fix:** Lazy import inside function body: `from open_webui.utils.chat import generate_chat_completion`
- **Where:** `backend/open_webui/routers/retrieval.py`
- **How to avoid:** Always lazy-import heavy utils in router files to break circular deps

## request.app.state.MODELS May Be Empty on First Call

- **When:** 2026-03-22
- **What:** `request.app.state.MODELS` dict is empty until `/api/models` endpoint is called at least once
- **Symptom:** Sub-chat extraction or index generation fails because model lookup returns None
- **Fix:** Call `/api/models` first to populate, or check and handle empty MODELS gracefully
- **Where:** `backend/open_webui/utils/middleware.py`, `backend/open_webui/routers/retrieval.py`

## PersistentConfig Requires `.value` Access Everywhere

- **When:** 2026-03-22
- **What:** When upgrading env vars to PersistentConfig, all references must use `.value`
- **Symptom:** Type errors, empty values, conditions that never trigger
- **Fix pattern:**
  - `X` -> `X.value`
  - `X is None` -> `not X.value`
  - `isinstance(X, str)` -> `isinstance(X.value, str) and X.value`
  - Default `""` (empty string) instead of `None` for "no value" semantic
  - In routers: after `app.state.config.X = value`, also call `ConfigModule.X.save()` on the imported PersistentConfig
- **Where:** `retrieval/utils.py` (~12 references), `routers/retrieval.py`, `config.py`

## ddgs==9.11.2 Yanked from PyPI

- **When:** 2026-03-22
- **What:** `pyproject.toml` pins `ddgs==9.11.2` which no longer exists on PyPI
- **Symptom:** `uv sync` fails entirely; cannot install dependencies normally
- **Fix (workaround):** Use `uv pip install "ddgs>=9.11"` to get `ddgs==9.11.4`, or run server via `.venv/bin/open-webui` directly
- **How to avoid:** Don't pin exact versions for rapidly-changing packages; use `>=X,<Y` ranges

## Svelte on:change Handler Must Be Async for Await

- **When:** 2026-03-21
- **What:** Zip upload handler in Skills.svelte used `await` but wasn't marked `async`
- **Symptom:** Syntax error in Svelte compilation
- **Fix:** Make the `on:change` handler `async`
- **Where:** `src/lib/components/workspace/Skills.svelte`

## StatusItem.svelte Uses Tabs for Indentation

- **When:** 2026-03-21
- **What:** Edit tool string matching breaks when tab indentation isn't exactly preserved
- **Symptom:** Edit operations fail with "string not found"
- **Fix:** Use Python string replacement for tab-indented Svelte files, or be very careful with Edit tool
- **Where:** `src/lib/components/chat/Messages/StatusHistory/StatusItem.svelte`

## OpenCode Model ID Format Conversion

- **When:** 2026-03-22
- **What:** Open WebUI uses `provider.model` format (dot separator), OpenCode uses `provider/model` (slash separator)
- **Symptom:** OpenCode can't find the model
- **Fix:** Convert via `.replace(".", "/", 1)` -- only replace FIRST dot
- **Where:** `backend/open_webui/utils/knowledge_export.py`

## threading.Semaphore vs asyncio.Semaphore

- **When:** 2026-03-22
- **What:** Must use the correct semaphore type based on sync/async context
- **Fix:** `threading.Semaphore` for synchronous code (KG1Loader.load()), `asyncio.Semaphore` for async code (sub-chat extraction)
- **How to avoid:** Check if the calling context is sync or async before choosing

## asyncio.run_coroutine_threadsafe() for Sync-to-Async Bridges

- **When:** 2026-03-22
- **What:** `process_file()` in retrieval.py is synchronous but needs to call async functions (generate_chat_completion)
- **Symptom:** Cannot use `await` in sync function
- **Fix:** Use `asyncio.run_coroutine_threadsafe(coro, request.app.state.main_loop).result()` to bridge
- **Where:** `backend/open_webui/routers/retrieval.py`

## CRITICAL: Frontend Build Output Must Go to backend/open_webui/frontend/

- **When:** 2026-03-22
- **What:** After `bun run build`, output must be copied to `backend/open_webui/frontend/`, NOT `backend/open_webui/static/`
- **Correct command:** `cp -rf build/* backend/open_webui/frontend/`
- **Evidence:** `.gitignore` line 313 ignores `/backend/open_webui/frontend` (generated dir)
- **How to avoid:** Always run `bun run build && cp -rf build/* backend/open_webui/frontend/` as a single pipeline after frontend changes

## CRITICAL: SIGTERM to uvicorn Does NOT Always Trigger FastAPI Lifespan Shutdown

- **When:** 2026-03-23
- **What:** Sending `kill` (SIGTERM) to the uvicorn process does not reliably execute FastAPI's `lifespan` async generator shutdown block
- **Symptom:** Subprocess cleanup code in lifespan shutdown never runs; orphaned OpenCode/organizer/research processes persist after server restart
- **Fix:** Register `atexit.register(_kill_all_subprocesses)` in `main.py` as a fallback alongside the lifespan shutdown hook
- **Pattern:** Always use BOTH lifespan shutdown AND atexit for critical cleanup (dual cleanup strategy)
- **Where:** `backend/open_webui/main.py`

## select.select() Doesn't Work with subprocess.PIPE in Python Text Mode

- **When:** 2026-03-23
- **What:** `select.select([proc.stdout, proc.stderr], ...)` raises errors or doesn't work reliably with subprocess PIPE file objects
- **Symptom:** Cannot do idle-based timeout detection using select-based multiplexing
- **Fix:** Use thread-based drain pattern: spawn separate threads for stdout and stderr that read line-by-line and update a shared `last_activity` timestamp. Main thread polls `last_activity` to detect idle timeout.
- **Where:** `backend/open_webui/utils/knowledge_export.py`, `backend/open_webui/utils/research.py`
- **How to avoid:** For subprocess output monitoring with idle detection, always use the thread-drain pattern, not select()

## Orphaned Subprocesses Accumulate Silently Without Tracking

- **When:** 2026-03-23
- **What:** OpenCode organizer processes spawned by knowledge_export.py were not tracked or cleaned up
- **Symptom:** After server restarts, multiple orphaned `opencode` processes consuming resources with no parent
- **Fix:** Track all spawned subprocesses in a module-level `_active_processes: set` with a `threading.Lock`, register cleanup function in both lifespan and atexit
- **Pattern:** Every module that spawns subprocesses must: (1) maintain a tracking set, (2) add on spawn, (3) discard on completion, (4) export a `kill_all_*()` function, (5) have that function called from main.py shutdown
- **Where:** `opencode.py`, `knowledge_export.py`, `research.py`, `main.py`

## CRITICAL: Qwen3.5-35B-A3B Is Unreliable for OpenCode Organizer File Ops

- **When:** 2026-03-24
- **What:** `lmstudio/unsloth/qwen3.5-35b-a3b` mutated exact CJK filenames during OpenCode knowledge-organization runs and also assumed shell `cd` state persisted across separate bash tool calls.
- **Symptom:** `read failed`, repeated `mv: No such file or directory`, endless loop with fresh output every few seconds so idle-timeout never trips.
- **Likely trigger:** exported markdown keeps `original_filename` metadata and human-readable headings that differ from the sanitized on-disk filename, so weaker tool-use models drift toward the pretty version.
- **Evidence:** `server.log` lines 852-853, 907-912, 921-1034.
- **Fix:** Do not let the model invent source filenames for `mv`; pass exact file pairs from Python and let the model decide only the destination category. Add stagnation / repeated-failure detection in `knowledge_export.py`.
- **Where:** `backend/open_webui/utils/knowledge_export.py`, `knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md`

## LM Studio Can List a Model That Still Fails to Load

- **When:** 2026-03-24
- **What:** `lms ls` showed `unsloth/qwen3.5-27b`, but an OpenCode planner run failed because LM Studio blocked model loading due to memory guardrails.
- **Symptom:** OpenCode returns an error event even though the model appears installed / listed.
- **Observed message:** model loading stopped due to insufficient system resources; LM Studio estimated ~95.58 GB required under current settings.
- **How to avoid confusion:** Separate "model exists" from "model can load right now". Verify both before switching defaults for critical paths.
- **Where:** Organizer planner test on 2026-03-24

## PersistentConfig Defaults Do NOT Override Saved DB Values

- **When:** 2026-03-24
- **What:** Changing backend default strings in `config.py` does not automatically change the live values if those `PersistentConfig` entries were already saved in the database.
- **Observed state:** After changing code defaults to `lmstudio.unsloth/qwen3.5-27b`, the live retrieval config API on port `28080` still returned `lmstudio.unsloth/qwen3.5-35b-a3b` for document index / research / organizer.
- **How to avoid confusion:** Distinguish between code defaults for fresh installs and persisted runtime settings for existing installs. If you need the live app to switch too, update the config through the admin/API or database.
- **Where:** `backend/open_webui/config.py`, `/api/v1/retrieval/config`

## Restarting Open WebUI From the Wrong CWD Can Change the Secret Key

- **When:** 2026-03-24
- **What:** Open WebUI loads `WEBUI_SECRET_KEY` from `Path.cwd() / .webui_secret_key` when the env var is not set.
- **Symptom:** Restarting from the repo root loaded `/Users/noelbao/Works/open-webui/.webui_secret_key` instead of `/Users/noelbao/Works/open-webui/knowledge-base/.webui_secret_key`, and existing JWTs immediately started returning `401 Unauthorized`.
- **Fix:** When manually restarting from the repo root, set `WEBUI_SECRET_KEY` explicitly from `knowledge-base/.webui_secret_key` before launching.
- **Where:** `backend/open_webui/__init__.py`, `server.log`

## Organizer Looks Fixed; KG1/OCR Timeouts Are Now the Main Failure Mode

- **When:** 2026-03-24
- **What:** A 30-round `server.log` observation after the organizer patch showed the organizer successfully planning and moving a CJK-named file, while the document-index path kept timing out against the OCR backend on `127.0.0.1:11434`.
- **Most important error:** `Document index generation failed ... TimeoutError`.
- **Pattern:** repeated `OCR API request error` / `Error during recognition` lines dominate the tail after large-PDF processing starts.
- **How to avoid misdiagnosis:** If organization succeeds and the log is still noisy, separate organizer issues from KG1 / Ollama OCR issues.
- **Where:** `server.log` lines 2118-2203

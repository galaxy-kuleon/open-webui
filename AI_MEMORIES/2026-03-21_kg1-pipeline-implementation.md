# KG1 Document Processing Pipeline - Implementation Log

## Date: 2026-03-21

## What was done

Implemented KG1 content extraction engine for Open WebUI that uses glm-ocr (local Ollama) for OCR-based document processing.

## Files Modified/Created

1. **NEW**: `backend/open_webui/retrieval/loaders/kg1.py` - KG1Loader with threading.Semaphore task queue
2. `backend/open_webui/config.py` - 7 KG1\_\* PersistentConfig variables
3. `backend/open_webui/main.py` - Import + app state init for KG1 configs
4. `backend/open_webui/routers/retrieval.py` - RAGConfigForm, GET/POST handlers, Loader kwargs, Markdown text splitter
5. `backend/open_webui/retrieval/loaders/main.py` - KG1 routing elif in \_get_loader()
6. `src/lib/components/admin/Settings/Documents.svelte` - KG1 UI panel, Ollama embedding datalist, Markdown splitter option

## Key Learnings

- glm-ocr CLI `--layout-device` doesn't accept "mps" - must use `GLMOCR_LAYOUT_DEVICE` env var instead
- glm-ocr requires `opencv-python` in its venv for layout detection to work (otherwise returns empty results)
- glm-ocr output: `{output_dir}/{stem}/{stem}.md` and `{output_dir}/{stem}/{stem}.json` (no imgs/ when layout-vis disabled)
- Open WebUI loader pattern: synchronous `load()` returning `List[Document]`
- threading.Semaphore (not asyncio.Semaphore) is correct for this use case since load() is synchronous
- **CRITICAL**: subprocess 呼叫 `uv run` 時必須清除 VIRTUAL_ENV, CONDA_PREFIX, CONDA_DEFAULT_ENV, PYTHONHOME, PYTHONPATH, UV_PROJECT_ENVIRONMENT 等 env vars，否則 uv 會找到錯誤的 Python 環境/版本

## Things to Remember

- glm-ocr project is at `/Users/noelbao/Works/glm-ocr-latest-test/` branch `kg/feat/local-ollama`
- Ollama models available: glm-ocr:bf16, qwen3-embedding:4b-fp16, qwen3-embedding:8b-fp16
- LibreOffice (soffice) is at `/opt/homebrew/bin/soffice`
- uv is at `/opt/homebrew/bin/uv`

## Status: COMPLETED

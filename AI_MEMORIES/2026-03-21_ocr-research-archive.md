# OCR Research Archive [ARCHIVED]

**Date:** 2026-03-21
**Status:** ARCHIVED -- Research completed, decisions made. See goals.md AG-1, AG-2 for outcomes.

---

## Summary of Research Conducted

Evaluated 3 OCR approaches for Open WebUI document extraction on macOS Apple Silicon:

### 1. DeepSeek-OCR / DeepSeek-OCR-2 [ABANDONED - see AG-1]

- **DeepSeek-OCR** (Oct 2025): 3B params MoE, Ollama-available, MIT license
- **DeepSeek-OCR-2** (Jan 2026): 3B params, Apache-2.0, better benchmarks (76.3 overall, 82.0 math, 79.0 multi-col)
- Best prompt: `<image>\n<|grounding|>Convert the document to markdown.`
- `<|grounding|>` token activates structured Markdown output (tables, headers, formulas)
- **macOS MPS:** Works with patches but slow (~1-2 min/image, ~19GB RAM). MLX path faster (~35s/10pg, ~5.5GB)
- **Blocked:** DeepSeek-OCR-2 not on Ollama; hosted API doesn't support vision; Flash Attention 2 is CUDA-only
- **Decision:** Too complex for integration. Chose glm-ocr via Ollama instead.

### 2. ocrmac (Apple Vision/LiveText) [ABANDONED - see AG-2]

- Python wrapper for Apple's native Vision + LiveText frameworks
- **Best config:** `framework='livetext', unit='line'` -- auto-detects CJK, fast (~130ms)
- **Fatal limitation:** Produces raw text only, no layout preservation, no tables, no Markdown structure
- **Decision:** Too simple. Need structured Markdown output for RAG quality.

### 3. glm-ocr via Ollama [CHOSEN]

- Local Ollama model `glm-ocr:bf16`, invoked via `uv run glmocr parse` subprocess
- Produces structured Markdown with tables, headers, formulas
- Supports PDF, images, Office docs (via LibreOffice conversion)
- MPS support via Ollama's Metal backend
- **Implemented as:** KG1Loader in `backend/open_webui/retrieval/loaders/kg1.py`

---

## Consolidated from original files:

- 2026-03-21_deepseek-ocr-research.md
- 2026-03-21_deepseek-ocr-guide.md
- 2026-03-21_deepseek-ocr-2-macos-mps-research.md
- 2026-03-21_deepseek-ocr2-macos-mps.md
- 2026-03-21_ocrmac_research.md
- 2026-03-21_ocrmac-research.md
- 2026-03-21_ocrmac-best-practice.md

# 2026-03-24 23:10 GLM-OCR Timeout Root Cause

## Goal

- Re-check the failure using `/tmp/owui-test.log` and the local `glm-ocr-latest-test` repo to determine whether the dominant problem is in Open WebUI or glm-ocr.

## Findings

- `/tmp/owui-test.log` shows the first PDF finished OCR successfully before the noisy failure started.
- The second PDF started glm-ocr at `22:38:03`, and the first burst of `OCR API request error ... read timeout=300` appears around `22:43:28`, which matches glm-ocr's `300s` per-request timeout expiring.
- The later `Document index generation failed ... TimeoutError` at `22:49:26` is real, but in this run it is downstream noise happening while glm-ocr is already stuck timing out on the second PDF.
- In glm-ocr, the self-hosted OCR client retries request failures with `retry_max_attempts=2` (3 total attempts) and logs exactly the same strings seen in the log.
- In glm-ocr's default config, self-hosted mode uses `request_timeout: 300`, `retry_max_attempts: 2`, and `max_workers: 32` while layout mode is enabled.
- Open WebUI's KG1 wrapper only sets host/port/mode/layout env vars; it does not currently tune glm-ocr self-hosted `request_timeout` or `max_workers`.

## Key Evidence

- `/tmp/owui-test.log:317` — first PDF OCR completed successfully.
- `/tmp/owui-test.log:322` — second PDF glm-ocr run started.
- `/tmp/owui-test.log:341` — first `OCR API request error ... read timeout=300` burst.
- `/tmp/owui-test.log:375` — later document-index timeout line.
- `glm-ocr-latest-test/glmocr/config.yaml:94` — self-hosted OCR request timeout `300`.
- `glm-ocr-latest-test/glmocr/config.yaml:97` — retries configured.
- `glm-ocr-latest-test/glmocr/config.yaml:109` — `max_workers: 32`.
- `glm-ocr-latest-test/glmocr/pipeline/pipeline.py:441` and `glm-ocr-latest-test/glmocr/pipeline/pipeline.py:489` — layout-mode region OCR submitted through a thread pool.
- `glm-ocr-latest-test/glmocr/ocr_client.py:278`, `glm-ocr-latest-test/glmocr/ocr_client.py:288`, `glm-ocr-latest-test/glmocr/ocr_client.py:362`, `glm-ocr-latest-test/glmocr/ocr_client.py:369` — total attempts, request timeout use, retry logging, and final error logging.
- `backend/open_webui/retrieval/loaders/kg1.py:345` — Open WebUI only injects basic glm-ocr env vars.

## Conclusion

- The dominant failure mode in this run is on the glm-ocr / Ollama side, not the organizer side.
- More specifically: one glm-ocr process with layout mode enabled is issuing many concurrent region OCR requests into local Ollama, and those requests are hitting glm-ocr's fixed self-hosted `300s` request timeout and retry loop.
- Open WebUI is still responsible for not exposing/tuning these glm-ocr knobs, but the immediate bottleneck is inside the glm-ocr self-hosted pipeline behavior and the Ollama service capacity behind it.

## Mitigation Ideas

- Lower glm-ocr self-hosted `pipeline.max_workers` aggressively for Ollama-backed runs (for example `1-4`, not `32`).
- Expose self-hosted `pipeline.ocr_api.request_timeout` via env / keyword override; right now common env mapping exposes MaaS timeout, not the self-hosted OCR timeout.
- Let Open WebUI pass a glm-ocr config file or explicit overrides so KG1 can tune `max_workers`, `request_timeout`, and possibly `api_mode` for Ollama.

## Expansion Log

- Replaced the earlier single-line diagnosis with a timeline-based cross-check against `/tmp/owui-test.log` and the glm-ocr source/config.
- Identified the exact glm-ocr timeout/retry logging site and the default `max_workers: 32` concurrency setting as the strongest root-cause clue.

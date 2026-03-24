# 2026-03-24 23:20 GLM-OCR Defaults Tuned

## Goal

- Apply the requested defaults in `/Users/noelbao/Works/glm-ocr-latest-test`: `pipeline.max_workers = 2` and timeout defaults = `600s`.

## Changes Made

- Updated SDK defaults in `glmocr/config.py`:
  - `OCRApiConfig.connect_timeout = 600`
  - `OCRApiConfig.request_timeout = 600`
  - `MaaSApiConfig.connect_timeout = 600`
  - `MaaSApiConfig.request_timeout = 600`
  - `PipelineConfig.max_workers = 2`
- Updated YAML defaults in `glmocr/config.yaml` to match.
- Updated test default timeout in `glmocr/tests/conftest.py` from `300` to `600`.
- Updated README snippets in `README.md` and `README_zh.md` to match the new timeout defaults.
- Updated the four skill CLI scripts so their `DEFAULT_TIMEOUT` is `600` instead of `60`.

## Verification

- `python3 -m py_compile glmocr/config.py glmocr/tests/conftest.py skills/glmocr/scripts/glm_ocr_cli.py skills/glmocr-table/scripts/glm_ocr_cli.py skills/glmocr-formula/scripts/glm_ocr_cli.py skills/glmocr-handwriting/scripts/glm_ocr_cli.py`
- Grep verification confirmed the new `600` timeout defaults and `max_workers: 2` values are present in the expected files.

## Notes

- The glm-ocr repo already had unrelated untracked items: `20260302_005648.jpg` and `results/`.
- No commits were created.

## Expansion Log

- Started from the user's requested knobs, mapped them to the SDK defaults, YAML defaults, integration-test default timeout, and user-facing skill CLI defaults so the repo stays internally consistent.

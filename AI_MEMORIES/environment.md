# Environment & Setup Notes

**Last updated:** 2026-03-24

## Development Machine

- **OS:** macOS (Apple Silicon)
- **Python:** Managed by `uv`
- **Node/Frontend:** `bun` (not npm/yarn)
- **Server port:** 28080

## CRITICAL: Frontend Build + Deploy Pipeline

After ANY frontend changes, run this exact sequence:

```bash
bun run format && bun run lint && bun run check   # Format, lint, type-check
bun run build && cp -rf build/* backend/open_webui/frontend/   # Build + deploy
```

- Output goes to `backend/open_webui/frontend/` -- **NOT** `backend/open_webui/static/`
- `.gitignore` line 313 ignores `/backend/open_webui/frontend` (it is a generated directory)
- If you copy to `static/` instead, the server will NOT serve updated frontend

## Tool Paths

| Tool                      | Path                                                                         |
| ------------------------- | ---------------------------------------------------------------------------- |
| uv                        | `/opt/homebrew/bin/uv`                                                       |
| LibreOffice (soffice)     | `/opt/homebrew/bin/soffice`                                                  |
| glm-ocr project           | `/Users/noelbao/Works/glm-ocr-latest-test/` (branch: `kg/feat/local-ollama`) |
| Knowledge base export dir | `/Users/noelbao/Works/open-webui/knowledge-base`                             |

## Ollama Models Available

| Model                     | Type                 | Size         |
| ------------------------- | -------------------- | ------------ |
| `glm-ocr:bf16`            | OCR (vision)         | --           |
| `qwen3-embedding:4b-fp16` | Embedding (dim=2560) | 4.02B params |
| `qwen3-embedding:8b-fp16` | Embedding (dim=4096) | 7.57B params |

## Current Server Configuration

| Setting                          | Value                                   |
| -------------------------------- | --------------------------------------- |
| Content Engine                   | kg1                                     |
| Embedding Engine                 | ollama                                  |
| Embedding Model                  | qwen3-embedding:4b-fp16                 |
| Embedding Query Prefix           | `Instruct: Given a web search query...` |
| Text Splitter                    | token                                   |
| Chunk Size                       | 16000                                   |
| Chunk Overlap                    | 4000                                    |
| Full Document Context            | True                                    |
| Document Index Generation        | True                                    |
| Index Model                      | lmstudio.qwen3.5-9b                     |
| Knowledge Export                 | True                                    |
| User Collection (Cross-Chat RAG) | True (RAG_USER_COLLECTION_ENABLED)      |

## Dependency Issues

### ddgs==9.11.2 Yanked from PyPI [ACTIVE]

- `uv sync` fails because `ddgs==9.11.2` no longer exists
- **Workaround:** Use `uv pip install "ddgs>=9.11"` to get 9.11.4
- **Or:** Run server via `.venv/bin/open-webui serve` directly (skip `uv run`)
- **Permanent fix needed:** Update `pyproject.toml` pin

### Missing System Dependencies

- `ffmpeg` not installed -- `pydub` warns at import (harmless but noisy)
- Fix: `brew install ffmpeg`

## KG1 Subprocess Environment Cleaning

When spawning `uv run glmocr parse` subprocess, MUST clean these env vars:

```
VIRTUAL_ENV, CONDA_PREFIX, CONDA_DEFAULT_ENV, PYTHONHOME, PYTHONPATH, UV_PROJECT_ENVIRONMENT
```

Without this, uv finds the wrong Python environment.

## OpenCode Integration Notes

- `opencode run` auto-approves all permissions (no TTY = no prompts)
- Config at project level: `opencode.json` with `"permission": "allow"`
- Skills at: `.opencode/skills/<name>/SKILL.md`
- Model ID format: Open WebUI uses `provider.model`, OpenCode uses `provider/model` -- convert with `.replace(".", "/", 1)`
- Verified via `lms ls` on 2026-03-24: LM Studio has `unsloth/qwen3.5-27b`
- Use that model as `lmstudio.unsloth/qwen3.5-27b` in Open WebUI and `lmstudio/unsloth/qwen3.5-27b` in OpenCode
- Important caveat: an OpenCode load attempt for `lmstudio/unsloth/qwen3.5-27b` hit an LM Studio memory guardrail on this machine (~95.58 GB required under current settings), so "listed" does not imply "loadable"

## Live Server Restart Notes

- Live server listens on port `28080`
- Graceful stop: send `SIGTERM` to the PID listening on `28080`
- Current known-good launch command from repo root: `WEBUI_SECRET_KEY=$(cat knowledge-base/.webui_secret_key) .venv/bin/open-webui serve --port 28080`
- Important: launching from repo root without setting `WEBUI_SECRET_KEY` loads `.webui_secret_key` from the repo root instead of `knowledge-base/.webui_secret_key`, which invalidates existing JWTs and causes `401 Unauthorized`
- Verified again on `2026-03-24`: graceful restart succeeded; server came back on PID `71341` and responded on `http://127.0.0.1:28080/api/version`; active log path was `/tmp/owui-test.log`

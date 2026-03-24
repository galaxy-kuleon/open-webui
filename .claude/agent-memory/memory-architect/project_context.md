---
name: Open WebUI Custom Fork Context
description: Project structure, memory organization patterns, and key context for Open WebUI custom fork with enhanced RAG, KG1, OpenCode integration
type: project
---

## Project

Custom fork of Open WebUI on branch `feat/opencode-agent-skill-integration`. Major additions: KG1 content extraction (glm-ocr via Ollama), enhanced RAG pipeline (full document context, sub-chat extraction, document index generation, per-user cross-chat RAG), OpenCode agent skills, knowledge export system, /research command.

**Why:** Building an intelligent document processing and knowledge management system on top of Open WebUI's chat interface.

**How to apply:** When restructuring memories, prioritize error records (subprocess env contamination, circular imports, PersistentConfig patterns) as these are the most commonly re-encountered issues. The user values detailed error documentation with symptoms and fixes.

## Memory Organization Patterns That Work

- The Six Sacred Categories structure maps well to this project: errors-and-lessons.md is the most referenced file
- Session implementation logs (dated) are useful as archives but should be compressed after 2 sessions
- OCR research files were the biggest source of redundancy -- consolidate research into single archive files early
- Keep environment.md updated with tool paths and current config as the user frequently switches between configs
- The user works in bilingual (English + Traditional Chinese) -- preserve Chinese comments and notes as-is
- Total memory size at end of 2026-03-22: 1048 lines across 13 files (well under 2000 budget)
- CRITICAL items belong in _INDEX.md header banner (e.g., uncommitted work warning) so they are impossible to miss

## Common Error Categories

1. **Subprocess environment contamination** -- parent venv vars leak into child processes
2. **Circular imports** -- Open WebUI has deep import chains; lazy imports are the solution
3. **Sync/async mismatch** -- threading.Semaphore vs asyncio.Semaphore, run_coroutine_threadsafe bridges
4. **PersistentConfig migration** -- every env var upgrade to PersistentConfig requires .value access updates across ~12 references
5. **Dependency pinning** -- PyPI packages get yanked (ddgs), breaking uv sync
6. **Subprocess lifecycle** -- orphaned processes from ungraceful shutdowns; always use tracking sets + dual cleanup (lifespan + atexit)
7. **Python I/O multiplexing** -- select.select() doesn't work with subprocess.PIPE text mode; use thread-drain pattern for idle timeout detection

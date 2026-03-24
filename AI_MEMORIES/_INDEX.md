# AI_MEMORIES Index

**Project:** Open WebUI (Custom Fork)
**Last Reorganized:** 2026-03-23 (end-of-session sync)
**Branch:** `feat/opencode-agent-skill-integration`
**CRITICAL:** All work from 2026-03-21 through 2026-03-23 is NOT committed. 30+ files changed across backend and frontend. See todos.md P0.

## Core Memory Files

| File                                           | Category            | Description                                                           |
| ---------------------------------------------- | ------------------- | --------------------------------------------------------------------- |
| [goals.md](goals.md)                           | Goals (1,2,3)       | Original goals, emergent goals, abandoned/adjusted goals with reasons |
| [errors-and-lessons.md](errors-and-lessons.md) | Errors (5)          | Mistakes, wrong approaches, critical gotchas, things to avoid         |
| [todos.md](todos.md)                           | Incomplete Work (4) | Open loops, unfinished items, prioritized                             |
| [unexplored.md](unexplored.md)                 | Unexplored (6)      | Ideas and directions not yet tried                                    |
| [architecture.md](architecture.md)             | Long-term           | Key architectural decisions, patterns, conventions                    |
| [environment.md](environment.md)               | Long-term           | Setup quirks, tooling notes, dependency issues                        |

## Feature Implementation Logs

| File                                                                                                 | Status    | Description                                                                   |
| ---------------------------------------------------------------------------------------------------- | --------- | ----------------------------------------------------------------------------- |
| [2026-03-21_kg1-pipeline-implementation.md](2026-03-21_kg1-pipeline-implementation.md)               | COMPLETED | KG1 content extraction engine (glm-ocr via Ollama)                            |
| [2026-03-21_opencode-agent-skill-integration.md](2026-03-21_opencode-agent-skill-integration.md)     | COMPLETED | OpenCode agent skills in Open WebUI Skills system                             |
| [2026-03-22_enhanced-rag-and-qwen3-embedding.md](2026-03-22_enhanced-rag-and-qwen3-embedding.md)     | COMPLETED | Full Document Context, Sub-Chat extraction, Qwen3 embedding, Knowledge Export |
| [2026-03-22_user-collection-cross-chat-rag.md](2026-03-22_user-collection-cross-chat-rag.md)         | COMPLETED | Per-user cross-chat RAG via user-{user_id} collection                         |
| [2026-03-23_idle-timeout-and-cleanup.md](2026-03-23_idle-timeout-and-cleanup.md)                     | COMPLETED | Idle-based subprocess timeout, Ollama notification, shutdown cleanup          |
| [2026-03-24_organizer-planner-and-27b-defaults.md](2026-03-24_organizer-planner-and-27b-defaults.md) | COMPLETED | Planner-only organizer refactor and default RAG model switch to Unsloth 27B   |
| [2026-03-24_server-log-30-round-observation.md](2026-03-24_server-log-30-round-observation.md)       | COMPLETED | 30 x 60s server.log observation shows organizer success but KG1/OCR timeouts  |

## Incident Investigations

| File                                                                                                     | Status       | Description                                                                           |
| -------------------------------------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------- |
| [2026-03-24_0020_opencode-qwen-organizer-failure.md](2026-03-24_0020_opencode-qwen-organizer-failure.md) | INVESTIGATED | Qwen-based OpenCode organizer loops on CJK filename drift and shell-state assumptions |

## Research Archives [ARCHIVED]

| File                                                                                                     | Topic                                                               |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| [2026-03-21_ocr-research-archive.md](2026-03-21_ocr-research-archive.md)                                 | Consolidated OCR research (DeepSeek-OCR, ocrmac, MPS compatibility) |
| [2026-03-22_opencode-permissions-skills-research.md](2026-03-22_opencode-permissions-skills-research.md) | OpenCode permissions, skills, non-interactive mode research         |

## Navigation

- **What are we building?** -> `goals.md`
- **What went wrong before?** -> `errors-and-lessons.md`
- **What's left to do?** -> `todos.md`
- **What haven't we tried?** -> `unexplored.md`
- **How does it all fit together?** -> `architecture.md`
- **How do I set up / run this?** -> `environment.md`

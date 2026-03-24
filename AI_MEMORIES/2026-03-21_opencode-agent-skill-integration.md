# OpenCode Agent Skill Integration — Implementation Log

**Date:** 2026-03-21
**Status:** Implementation Complete

## What Was Done

Implemented full integration of OpenCode agent skills into Open WebUI's Skills system across 4 phases:

### Phase 1: Backend Foundation

- Extended `SkillMeta` model with `type` (None | "agent_skill") and `disk_path` fields
- Created `backend/open_webui/utils/opencode.py` — sandbox setup, config generation, execution with streaming, output file collection, cleanup
- Added `OPENCODE_PATH` config to `env.py`
- Added `POST /skills/upload-zip` endpoint for uploading agent skill zips
- Added disk cleanup on agent skill deletion

### Phase 2: Builtin Tool & Middleware

- Added `run_agent_skill` builtin tool in `tools/builtin.py`
- Modified middleware (`utils/middleware.py`) to branch on `agent_skill` type — injects `<agent_skill>` instructions instead of full content
- Registered `run_agent_skill` in `utils/tools.py` when `__agent_skill_ids__` present

### Phase 3: Frontend

- Accepted `.zip` in Skills import input (`Skills.svelte`)
- Added `uploadSkillZip()` API function (`skills/index.ts`)
- Added "Agent" badge for agent skills in the skill list
- Built and deployed frontend to backend

### Phase 4: Polish

- All Python files pass syntax checks
- Frontend builds successfully
- Proper error handling: zip bomb protection, timeout, concurrent execution limit

## Files Modified

| File                                         | Change                                                       |
| -------------------------------------------- | ------------------------------------------------------------ |
| `backend/open_webui/models/skills.py`        | Added `type`, `disk_path` to SkillMeta                       |
| `backend/open_webui/routers/skills.py`       | Added upload-zip endpoint, deletion cleanup                  |
| `backend/open_webui/utils/opencode.py`       | **New** — sandbox, config gen, execution, output collection  |
| `backend/open_webui/env.py`                  | Added OPENCODE_PATH                                          |
| `backend/open_webui/tools/builtin.py`        | Added `run_agent_skill` function                             |
| `backend/open_webui/utils/middleware.py`     | Branch agent_skill in skill injection, register builtin tool |
| `backend/open_webui/utils/tools.py`          | Import + register `run_agent_skill`                          |
| `src/lib/components/workspace/Skills.svelte` | Accept .zip, agent skill badge                               |
| `src/lib/apis/skills/index.ts`               | Added `uploadSkillZip()`                                     |

## Key Architecture Decisions

- Skill files are **copied** (not symlinked) into sandbox for isolation
- `AGENTS.md` is placed at sandbox root (opencode reads it as project-level instructions)
- opencode config (`~/.config/opencode/opencode.json`) is global, auto-generated from OpenWebUI providers
- Full permissions (`"*": "allow"`) inside sandbox — safe because cwd-confined
- Concurrent execution limit: 2 per user via `asyncio.Semaphore`
- Default timeout: 300s

## Rich Agent Skill Renderer (Added)

### Backend changes:

- `opencode.py` events now include `sub_action` field: `start`, `thinking`, `tool_use`, `output`, `error`, `complete`
- Tool use events include structured `tool_name` and `tool_input` fields
- `builtin.py` start/complete events also have `sub_action` + `skill_name`

### Frontend new component:

- **`AgentSkillStatus.svelte`** — dedicated renderer for agent skill status events:
  - `start`: Blue sparkles icon, skill name with emphasis
  - `thinking`: Purple lightbulb icon, collapsible monospace content with purple tint
  - `tool_use`: Amber terminal icon, tool name label, collapsible monospace input
  - `output`: Green bolt icon, collapsible output text
  - `error`: Red warning triangle, error message
  - `complete`: Green checkmark circle, completion message

### Frontend modifications:

- **`StatusItem.svelte`**: Added `agent_skill` action case delegating to `AgentSkillStatus`
- **`StatusHistory.svelte`**: Auto-expands timeline when agent skill events are present

### Files:

| File                                    | Change                              |
| --------------------------------------- | ----------------------------------- |
| `StatusHistory/AgentSkillStatus.svelte` | **New** — rich renderer             |
| `StatusHistory/StatusItem.svelte`       | Added agent_skill case + import     |
| `StatusHistory.svelte`                  | Auto-expand for agent skills        |
| `backend/open_webui/utils/opencode.py`  | Structured events with sub_action   |
| `backend/open_webui/tools/builtin.py`   | sub_action on start/complete events |

## Errors / Lessons

- Svelte `on:change` handler needed to be `async` for the zip upload (uses `await`)
- The `agent_skill_ids` list had to be initialized BEFORE the `if all_skill_ids:` block since it's referenced after
- The pre-existing `uv` dependency issue (ddgs==9.11.2) is unrelated to our changes
- StatusItem.svelte uses tabs for indentation — Edit tool tab matching can be fragile, Python string replacement is more reliable

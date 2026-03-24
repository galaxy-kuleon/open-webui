# OpenCode Permissions, Skills, and Non-Interactive Mode Research

**Date:** 2026-03-22
**Status:** RESEARCH COMPLETE
**Sources:** opencode.ai/docs/permissions, opencode.ai/docs/skills, opencode.ai/docs/cli, opencode.ai/docs/config, opencode.ai/docs/tools, opencode.ai/docs/agents, opencode.ai/docs/modes, GitHub issues #9070, #11831, #8463, PR #6968

---

## Key Findings

### 1. Permissions System

OpenCode uses a granular permission system with 3 states: `allow`, `ask`, `deny`.

**Permission types:** read, edit, glob, list, external_directory, bash, skill

**Config approach (opencode.json):**

```json
{
	"permission": "allow" // allow ALL operations (YOLO equivalent)
}
```

Or granular:

```json
{
	"permission": {
		"*": "allow",
		"bash": { "*": "ask", "git *": "allow", "rm *": "deny" },
		"edit": { "*.mdx": "allow" }
	}
}
```

### 2. Non-Interactive Mode (opencode run)

**CRITICAL FINDING:** When using `opencode run`, all permissions are auto-approved for the session. No TTY = no prompts = everything allowed.

### 3. YOLO Mode (NOT YET MERGED as of 2026-03-22)

- Issue #11831 is OPEN (not merged)
- `OPENCODE_YOLO=true` env var
- `--yolo` CLI flag
- `"yolo": true` in opencode.json
- Explicit `deny` rules are ALWAYS respected even in YOLO mode
- PR #9073 was CLOSED without merge

### 4. Current Best Practice for Auto-Approval

Use `"permission": "allow"` in opencode.json. This is the documented equivalent of Claude Code's `--dangerously-skip-permissions`.

### 5. Skills System

Skills are SKILL.md files with YAML frontmatter. Stored in:

- `.opencode/skills/<name>/SKILL.md` (project)
- `~/.config/opencode/skills/<name>/SKILL.md` (global)
- Also supports `.claude/skills/` and `.agents/skills/` paths

### 6. Config File Locations (merge order)

1. Remote config (.well-known/opencode)
2. Global (~/.config/opencode/opencode.json)
3. OPENCODE_CONFIG env var
4. Project root (opencode.json)
5. .opencode directories
6. OPENCODE_CONFIG_CONTENT env var (inline JSON)

---

## Action Items for Our Integration

- For subprocess usage: `opencode run` auto-approves everything - safe for background use
- For config: set `"permission": "allow"` in opencode.json as safety net
- For skills: create `.opencode/skills/knowledge-organizer/SKILL.md`
- For model: set `"model": "provider/model"` in opencode.json or use `-m` flag

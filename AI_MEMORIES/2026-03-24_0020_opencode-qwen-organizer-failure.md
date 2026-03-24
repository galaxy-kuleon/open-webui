# OpenCode + Qwen Organizer Failure Investigation

## Date: 2026-03-24 00:20

**Status:** INVESTIGATED

## What Happened

- Investigated `server.log` for the knowledge organizer running through OpenCode with model `lmstudio/unsloth/qwen3.5-35b-a3b`.
- OpenCode itself did not crash; the failure is in the model's tool use during organizer runs.
- The model repeatedly mutated exact CJK filenames by inserting spaces around tokens and hyphens, despite the skill explicitly saying not to do that.
- The model also assumed shell state persisted across bash calls. After a `cd .../inbox && ls -la`, later `mv` calls dropped the `inbox/` prefix and failed because the next tool call started from the knowledge-base root again.
- The exported files themselves contain human-friendly metadata (`original_filename`) and headings like `113 年度...` that differ from the sanitized on-disk filename `113年度...`; Qwen appears to copy the human-readable form instead of the real path.
- Because the model kept emitting output every few seconds, the 600s idle timeout never triggered, so the organizer loop stayed alive instead of self-terminating.

## Evidence

- `server.log:852`-`server.log:853`: `read failed` because the model changed `113年度...-1.index.md` into `113 年度 ... -1.index.md`.
- `server.log:855`-`server.log:858`: `ls` showed the exact real `.index.md` filename with no added spaces.
- `knowledge-base/inbox/評鑑_教學區域醫院指標_113年度教學醫院評鑑基準及評量項目-區域醫院-地區醫院適用-1.md:3`: frontmatter keeps the original filename with spaces / punctuation.
- `knowledge-base/inbox/評鑑_教學區域醫院指標_113年度教學醫院評鑑基準及評量項目-區域醫院-地區醫院適用-1.md:7`: document heading contains `113 年度`, which likely nudges the model toward a non-literal path.
- `server.log:907`-`server.log:912`: repeated `mv` attempts with hallucinated spaces failed.
- `server.log:913`-`server.log:920`: the model inspected `inbox/` correctly.
- `server.log:921`-`server.log:1034`: the model alternated between wrong spaced filenames and root-relative moves without `inbox/`, causing an endless `mv: No such file or directory` loop.
- `knowledge_export.py:220`-`knowledge_export.py:320`: organizer only has idle-timeout protection, not stagnation / repeated-failure detection.
- `knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md:13`-`knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md:14`: the skill already warns to use exact filenames and not add spaces, but Qwen ignored it.

## Conclusions

- This is a poor model-tooling fit, not an OpenCode runtime crash.
- `qwen3.5-35b-a3b` is unreliable for autonomous filesystem operations involving long CJK filenames.
- The current organizer architecture is too permissive: it lets the model invent shell commands instead of constraining it to exact backend-provided paths.
- Idle-timeout alone is insufficient for agent loops that keep producing failing output.

## Recommended Fix Direction

1. Do not let the model synthesize source filenames for moves; pass exact file pairs from Python and let the model only choose destination categories.
2. Add stagnation detection in `knowledge_export.py` for repeated identical failures / repeated identical commands.
3. Prefer a stronger tool-use model for the organizer, especially when filenames contain CJK or punctuation.
4. Optionally add a post-step validator: if files remain in `inbox/` after N failed move attempts, abort and surface a clear organizer error.

## Expansion Log

- Read memories: `AI_MEMORIES/2026-03-22_opencode-permissions-skills-research.md`, `AI_MEMORIES/2026-03-21_opencode-agent-skill-integration.md`, `AI_MEMORIES/errors-and-lessons.md`
- Inspected logs: `server.log`
- Inspected code: `backend/open_webui/utils/knowledge_export.py`, `knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md`
- Verified live state: organizer process still running more than 12 minutes after launch while looping on failed `mv` commands

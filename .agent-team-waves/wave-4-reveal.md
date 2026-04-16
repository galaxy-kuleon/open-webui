# Wave 4 - Reveal Packet

**Wave Objective**: Achieve lockfile/packaging consistency and add broader Hermes integration test coverage to close the remediation plan
**Data-flow segment**: output + error-path (build toolchain + test surface)
**Blast radius**: moderate-to-system-wide (dependency graph + new test files)
**Total waves**: 4 of 4 (final wave)

## Spec Slice (from remediation plan)

### From Remediation WS7 — Lockfile and Packaging Consistency

Implementation Tasks:
1. Update `package-lock.json` to match the current `package.json`.
2. Verify `@playwright/test` and any related resolution changes are correctly captured.
3. Confirm the `build` script copy step is intentional and still valid.

Acceptance Criteria:
1. `package.json` and `package-lock.json` are in sync.
2. Fresh install does not mutate the lockfile.
3. CI and local frontend commands use a consistent dependency graph.

### From Remediation WS8 — Hermes Integration Coverage

Implementation Tasks:
1. Add tests for `backend/open_webui/utils/builtin_pipes.py`:
   - no users -> no registration
   - first user exists -> registration
   - existing builtin with matching hash -> no write
   - content hash change -> update
2. Add tests for Hermes manifold discovery in `hermes_agent.py`.
3. Add tests for custom SSE event translation (hermes.tool.progress → Open WebUI status events).
4. Add tests for reasoning/status emission behavior if retained.

### From Remediation Plan — Definition of Done

The branch is done when:
1. Known parity gaps are closed (Waves 1-3 done).
2. Hermes integration is not broken in default setup (Wave 1 done).
3. A features ported server-side are reachable from UI (Waves 1-3 done).
4. Branch no longer carries unexplained drift (Wave 2 done).
5. Focused regression coverage exists for Hermes-specific surface (THIS WAVE).

## Deferred Items Assigned To This Wave

None

## Constraints for This Wave

- **Allowed files to modify**: `package-lock.json` (regeneration), `backend/open_webui/test/` (new test files), existing test files if extending
- **Forbidden files**: `src/` (all frontend code — Waves 1-3 complete), `backend/open_webui/pipes/hermes_agent.py` (production code, do not modify), `backend/open_webui/routers/` (production code)
- **Out-of-scope**: Frontend changes, backend production code changes

## Handoff from Wave 3

Wave 3 closed COMPLETE. All 9 RAG/embedding settings visible and editable. No deferred items.

## Critical Discovery Context

### Lockfile State

- `package.json` vs main: version 0.8.12, new packages (@playwright/test, jszip, shiki, sql.js, xterm packages), version bumps (svelte ^5.53.10, bits-ui ^2.0.0, svelte-confetti ^2.3.2), build script adds `cp -rf build/* backend/open_webui/frontend/`
- `package-lock.json` exists (552KB), diff vs main is 15,191 lines
- Need to verify: `npm install` produces no further diff (lockfile is in sync)

### Existing Hermes Tests

`test_hermes_pipe.py` (153 lines, 4 tests):
1. `test_maybe_add_session_header_requires_api_key` — unit test for _maybe_add_session_header
2. `test_pipe_skips_session_header_without_api_key` — integration with fake client
3. `test_pipe_adds_session_header_with_api_key` — integration with fake client
4. `test_pipe_403_session_continuity_error_includes_hint` — 403 error path

`test_file_upload_image_analysis.py` (62 lines, 1 test):
1. Image analysis dispatch when enabled

### Missing Coverage (from WS8)

1. **Builtin pipe bootstrap** (`backend/open_webui/utils/builtin_pipes.py`): no tests exist
2. **Manifold discovery** (Pipe.pipes() method): no tests
3. **SSE event translation**: hermes.tool.progress → status events, no tests
4. **Reasoning/status emission**: no tests

## Success Criteria

- `npm install` produces no lockfile diff (package.json and package-lock.json in sync)
- `bun run build` still works after lockfile update
- Builtin pipe bootstrap has test coverage for the 4 specified scenarios
- Hermes manifold discovery has at least 1 test
- SSE event translation has at least 1 test
- All new and existing backend tests pass
- Definition of Done checklist from remediation plan is satisfied

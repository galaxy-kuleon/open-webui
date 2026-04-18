# Wave 1 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Remove dead `reasoning_content`/`reasoning` branch from `hermes_agent.py`, add a regression test proving such a chunk now passes through as plain content without emitting `thinking` status, and document the Connection-vs-pipe tradeoff in the `pipe()` docstring.
**Turns executed**: 3 (of budget 3)
**Master directives issued**: 2 (after T1, after T2)
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-17

## Turn Log

### T1

- **Master directive for this turn**: Execute T1 — delete dead branch, add regression test. Normal 3-turn budget; no escalation basis.
- **Principal work**:
  - `backend/open_webui/pipes/hermes_agent.py:212-220` — deleted old `reasoning_content`/`reasoning` branch. Try body now reads `chunk = json.loads(data_str); yield chunk` followed by `except json.JSONDecodeError: log.warning(...)`.
  - `backend/open_webui/test/utils/test_hermes_pipe.py:156-197` — appended `test_pipe_passes_through_reasoning_content_unchanged`. Test sends `{"choices":[{"delta":{"reasoning_content":"thinking out loud","content":"hi"}}]}` and asserts (a) yielded chunk equals expected with `reasoning_content` intact, (b) no emitter event has `sub_action == 'thinking'`.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `rg 'reasoning_content|reasoning|thinking' backend/open_webui/pipes/hermes_agent.py` — 0 matches
  - standalone adversarial probe at `/tmp/probe_reasoning_field.py` confirming bare `reasoning` field also passes through correctly (not just `reasoning_content`)
  - `env PYTHONPATH=backend uv run pytest backend/open_webui/test/utils/test_hermes_pipe.py backend/open_webui/test/utils/test_file_upload_image_analysis.py backend/open_webui/test/utils/test_builtin_pipes.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py backend/open_webui/test/utils/test_hermes_tool_progress.py -v` — 18 passed
  - SSE state-machine correctness check
  - import-hygiene audit
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - Regression test is a genuine guard: both assertions would fail if the deleted branch were restored, because `delta.pop('reasoning_content')` mutates the in-place delta dict and `_emit_status('thinking', ...)` would fire.
  - T1 evaluator adversarial probe: bare `reasoning` field (distinct from `reasoning_content`) also passes through correctly via the collapsed try body — no in-repo assertion covers this, classified as coverage enhancement not defect.
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_hermes_pipe.py`

### T2

- **Master directive for this turn**: PROCEED AS PLANNED. T1 PASSed cleanly. Evaluator's bare-`reasoning` coverage-enhancement suggestion classified out-of-scope for T2 (T2 forbids new tests) and out-of-scope for T3 (T3 is global-consistency, not coverage expansion). Left as post-wave user-taste decision.
- **Principal work**:
  - `backend/open_webui/pipes/hermes_agent.py:94-121` — extended `pipe()` docstring from 8 lines to 28 lines. Added "Connection vs. pipe" paragraph enumerating 4 pipe-only features:
    1. tool-progress translation — decodes `event: hermes.tool.progress` SSE frames
    2. file-path injection — resolves Open WebUI upload IDs to absolute paths, injects into system prompt
    3. manifold discovery — enumerates sub-models via `/v1/models`, strips `pipe_id.` prefix
    4. session-header gate — conditionally sends `X-Hermes-Session-Id` only when `hermes_api_key` configured
  - Contract-alignment revisit of T1: confirmed try body still collapsed; no stale references introduced.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `git diff HEAD -- hermes_agent.py` — confirmed change is docstring-only (T1 deletion accumulated from working tree)
  - Mapped each of 4 features to live code: tool-progress @ lines 200-220, file-path injection via `_resolve_file_paths` + `_inject_file_context` @ lines 134/283/310, manifold discovery @ lines 57-79/141, session-header gate @ `_maybe_add_session_header`/hint @ line 182
  - Read `functions.py:167-183` (`process_line`) to independently verify the "OpenAI SSE parser drops non-`data:` events" claim
  - Full 18-test gate — 18 passed
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - All 4 docstring features mapped to live implementations with no contract mismatch.
  - Cosmetic wording observation: "silently drops" is a mild simplification (non-`data:` events fall through to `else`, getting wrapped as fake content chunks rather than literally dropped; net effect for `event:` lines is equivalent). Adjudicated substantively accurate by kind 3 — not a defect.
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py`

### T3

- **Master directive for this turn**: PROCEED AS PLANNED. T2 PASSed cleanly. "Silently drops" wording adjudicated substantively accurate; forbidden from re-wording at T3.
- **Principal work**:
  - Re-read both modified files end-to-end. No edits made — no coherence defect found.
  - Global-consistency lens: all 4 docstring features confirmed to have live implementations; no orphaned imports; regression test confirmed to be a genuine guard.
  - Reported 2 out-of-wave carry observations: (a) bare `reasoning` field has no in-repo assertion, (b) timeout-mock note.
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `env PYTHONPATH=backend uv run pytest backend/open_webui/test/utils/test_hermes_pipe.py backend/open_webui/test/utils/test_file_upload_image_analysis.py backend/open_webui/test/utils/test_builtin_pipes.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py backend/open_webui/test/utils/test_hermes_tool_progress.py -v` — 18 passed
  - `rg 'reasoning_content|reasoning|thinking' backend/open_webui/pipes/hermes_agent.py` — 0 matches
  - `git diff --stat HEAD` — only 2 production files modified
  - Mutation proof: walked through what would happen if the deleted branch were restored — both regression-test assertions (chunk equality + no-thinking) would fail
  - Mock-infrastructure spot-check: `_FakeResponse.aiter_lines` and `_FakeClient.stream` correctly satisfy async-context + async-generator protocols the pipe consumes; test passes for the right reason, not vacuously
  - Docstring-to-code tour: each of 4 features independently mapped to file:line
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - Mutation proof confirms regression test is a genuine guard, not vacuous.
  - T3 principal's 2 carry items classified as out-of-wave observations by kind 2; do not block wave close.
- **Cross-turn findings**:
  - The collapsed try body at `hermes_agent.py:212` is the single structural change; docstring at `hermes_agent.py:94-121` is the second change. These two sites are the complete blast radius of Wave 1.
  - Test count moved from 17 to 18 across the wave; the new test at `test_hermes_pipe.py:156-197` is the only new assertion in the repo.
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_hermes_pipe.py`

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

None

## What Was Considered But Not Tried (Deferred)

- **Bare `reasoning` field in-repo assertion** — T1 evaluator's adversarial probe at `/tmp/probe_reasoning_field.py` confirmed correctness for the bare `reasoning` field (distinct from `reasoning_content`), but adding an in-repo test assertion was forbidden at T2 (no new tests) and T3 (global-consistency only). Left as a user-taste decision post-wave.
- **"Silently drops" wording tightening** — T2 evaluator noted the phrase is a mild simplification; "falls through to else and gets wrapped as a fake content chunk" would be more precise. Kind 3 adjudicated the current wording as substantively accurate and explicitly forbade rewording at T3.

## What Was Given Up

None. Wave closed clean.

## Deferred Queue For Replanning

None.

## Unresolved Findings

None — wave closed clean.

**Out-of-scope carry observations** (non-blocking; not deferred to Wave 2 Playwright scope; candidates for a future wave or direct user edit):

- `test_hermes_pipe.py` has no assertion covering the bare `reasoning` field (only `reasoning_content`). The T1 evaluator's standalone probe at `/tmp/probe_reasoning_field.py` confirmed the code handles it correctly, but the repo lacks a regression guard for that specific field name.
- Timeout-mock note: the mock infrastructure in `test_hermes_pipe.py` does not exercise timeout paths in the async stream reader; not a defect but a coverage gap.

## Files Modified (absolute paths)

- `/Users/noelbao/Works/open-webui/backend/open_webui/pipes/hermes_agent.py` — deleted lines 212-220 (dead `reasoning_content`/`reasoning` branch); extended `pipe()` docstring at lines 94-121 (net +20 lines)
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/utils/test_hermes_pipe.py` — appended regression test at lines 156-197 (+42 lines, 1 new test: `test_pipe_passes_through_reasoning_content_unchanged`)

## Behavioral Verifications Run

- `env PYTHONPATH=backend uv run pytest backend/open_webui/test/utils/test_hermes_pipe.py backend/open_webui/test/utils/test_file_upload_image_analysis.py backend/open_webui/test/utils/test_builtin_pipes.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py backend/open_webui/test/utils/test_hermes_tool_progress.py -v` — **18 passed** (was 17 before Wave 1); run at T1, T2, T3
- `rg 'reasoning_content|reasoning|thinking' backend/open_webui/pipes/hermes_agent.py` — **0 matches**; run at T1 and T3
- `git diff --stat HEAD` — **2 production files modified** only; run at T3
- Standalone adversarial probe `/tmp/probe_reasoning_field.py` (T1 evaluator) — bare `reasoning` field pass-through confirmed correct
- Mutation proof walkthrough (T3 evaluator) — both regression-test assertions proven to fail if the deleted branch were restored: `chunk == expected_chunk` fails because `delta.pop('reasoning_content')` mutates in-place, and the no-thinking assertion fails because `_emit_status('thinking', ...)` fires

## Wave Summary

Wave 1 removed the dead `reasoning_content`/`reasoning` branch from `backend/open_webui/pipes/hermes_agent.py:212-220`, collapsing the try body to `chunk = json.loads(data_str); yield chunk`. A regression test at `backend/open_webui/test/utils/test_hermes_pipe.py:156-197` guards this: it asserts that a chunk containing `reasoning_content` passes through unchanged and that no emitter event carries `sub_action == 'thinking'`; the T3 evaluator's mutation proof confirmed both assertions would fail if the branch were restored.

The `pipe()` docstring at `backend/open_webui/pipes/hermes_agent.py:94-121` was extended from 8 to 28 lines with a "Connection vs. pipe" paragraph enumerating four pipe-only features (tool-progress translation, file-path injection, manifold discovery, session-header gate), each independently verified by the T2 evaluator against live code at their respective file:line locations.

The wave ran 3 turns with zero retries, zero carry, and zero unresolved blockers. Test count moved from 17 to 18. Two out-of-scope carry observations were noted (bare `reasoning` field has no in-repo assertion; timeout-mock coverage gap) but classified as non-blocking candidates for a future wave, not for Wave 2 (Playwright smoke tests).

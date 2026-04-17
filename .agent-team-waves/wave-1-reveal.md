# Wave 1 — Reveal Packet

**Wave Objective**: Remove dead `reasoning_content` branch from `hermes_agent.py`, add a regression test proving the chunk now passes through as plain content without emitting `thinking` status, and document the Connection-vs-pipe tradeoff in the `pipe()` docstring.
**Data-flow segment**: error-path (dead-code removal + docs + regression test)
**Blast radius**: smallest-isolated
**Total waves**: 1 of 2

## Spec Slice (from qa-planner memo)

Verbatim from `/Users/noelbao/.claude-kg/plans/dynamic-snacking-sprout.md`:

> **The gaps (relevant to Wave 1):**
>
> 1. **Dead code** in `backend/open_webui/pipes/hermes_agent.py:212-220` — `reasoning_content` / `reasoning` extraction fires never against real Hermes (Hermes `/v1/chat/completions` does not emit these fields; confirmed in `../hermes-agent/gateway/platforms/api_server.py:840-970`). Leaving it invites confusion about what the pipe does.
>
> 2. **Ambiguous docstring** — no guidance on Connection-vs-pipe tradeoff. Users with no tool-progress / file-injection / manifold / session-header needs could use Hermes as a plain OpenAI Connection and skip the pipe entirely. The pipe docstring should say this.

> ### Wave 1 — Pipe cleanup + docstring clarification
>
> **Blast radius:** smallest-isolated (single file, ~15 lines)
> **Data-flow segment:** error-path (test coverage + docs)
>
> | Turn | Primary Scope | Revisit | Lens | Notes |
> |------|---------------|---------|------|-------|
> | T1 | Delete lines 212-220 in `hermes_agent.py` (reasoning branch). Add regression test to `test_hermes_pipe.py` asserting a chunk with `reasoning_content` is yielded as-is without triggering a `thinking` status event. | — | — | Safe deletion — `_emit_status` has 6 other call sites (start, complete, error ×4), not orphaned |
> | T2 | Add Connection-vs-pipe clarification to `pipe()` docstring at line 100 (after the stateless-fallback line). Note the 4 pipe-only features (tool progress translation, file-path injection, manifold discovery, session header gate). | T1 | contract-alignment | Revisit ensures the deleted branch really isn't referenced anywhere |
> | T3 | Full cross-validation: all 18 tests pass, docstring reads coherently, no orphaned imports. | T1, T2 | global-consistency | |
>
> **Files modified:**
> - `backend/open_webui/pipes/hermes_agent.py` (delete ~10 lines, docstring ~5 lines)
> - `backend/open_webui/test/utils/test_hermes_pipe.py` (add 1 test, ~25 lines)

## Deferred Items Assigned To This Wave

None — first wave of this run.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/pipes/hermes_agent.py` (delete lines 212-220, edit docstring at line 94-101)
  - `backend/open_webui/test/utils/test_hermes_pipe.py` (add one regression test)
- **Forbidden files**: any `src/*` frontend file, any other backend file (routers, config, other pipes), `package.json`, `package-lock.json`
- **Wave-specific forbidden changes**: do not touch valve definitions; do not refactor `_emit_status` or its call sites; do not change the mixed-SSE parser loop structure at lines 171-225
- **Out-of-scope items (deferred to future waves)**: none — Wave 2 is Playwright smoke tests, unrelated to this wave's scope

## Handoff from Wave N-1

This is the first wave of the follow-up /atw run. The prior /atw run on the same branch closed COMPLETE on 2026-04-17 with 4 commits (`93fcc24` → `9d803e8`). That run's artefacts are archived at `./.agent-team-waves/archive-2026-04-17-hermes-port-main/`.

Concrete code anchors relevant to this wave:

- **Target dead code** — `backend/open_webui/pipes/hermes_agent.py:209-225`:
  ```python
  # Standard OpenAI chunk — yield as dict for process_line
  try:
      chunk = json.loads(data_str)
      # Check for reasoning content and emit as thinking
      delta = (chunk.get('choices') or [{}])[0].get('delta', {})
      reasoning = delta.pop('reasoning_content', None) or delta.pop('reasoning', None)
      if reasoning:
          await self._emit_status(
              __event_emitter__,
              'thinking',
              reasoning[:500],
          )
      yield chunk
  except json.JSONDecodeError:
      log.warning(f'Bad SSE JSON: {data_str}')
  ```
  After deletion, the `try` body should collapse to:
  ```python
  try:
      chunk = json.loads(data_str)
      yield chunk
  except json.JSONDecodeError:
      log.warning(f'Bad SSE JSON: {data_str}')
  ```

- **`pipe()` docstring** — `hermes_agent.py:94-101` currently reads:
  ```
  Forward chat to hermes and stream the response.

  Yields OpenAI-format chunk dicts for content streaming.
  Emits status events via __event_emitter__ for tool progress.
  Falls back to stateless request-body history when no API key is
  configured, because Hermes only accepts session continuation on
  authenticated requests.
  ```
  T2 must extend this with a `Connection vs. pipe:` section explaining:
  1. Hermes is OpenAI-compatible on `/v1/chat/completions` and `/v1/models`; users who don't need the 4 pipe-only features can configure Hermes as a plain OpenAI Connection and skip this pipe entirely.
  2. The 4 pipe-only features: (a) `hermes.tool.progress` SSE event translation (Open WebUI's parser can't handle custom `event:` types), (b) uploaded file-path injection into system prompt (`_resolve_file_paths` + `_inject_file_context`), (c) manifold sub-model prefix stripping (`hermes_agent.profile` → `profile`), (d) auth-gated `X-Hermes-Session-Id` header (Hermes 403s if `API_SERVER_KEY` is missing and the header is sent).

- **Existing test file** — `backend/open_webui/test/utils/test_hermes_pipe.py` (153 lines, 4 tests using `_FakeResponse`/`_FakeClient`/`_client_factory` fixtures at lines 9-62). The new regression test should follow the same pattern: feed a `_FakeResponse` with an SSE `data:` line containing a chunk whose `delta` has `reasoning_content`, collect both yielded chunks AND emitter events, assert the chunk is yielded as-is AND no `thinking` status is emitted.

- **Cross-reference**: `_emit_status` at `hermes_agent.py:320+` is called from 6 other sites (start line 134, complete line 228, three error-path sites at lines 167, 233, 239, 245). Deletion of the `thinking` call does not orphan the method.

## Success Criteria

Every item below must be independently verifiable by kind 2:

1. Lines 213-220 of `hermes_agent.py` (the `delta = ... reasoning = ... if reasoning:` block plus the `_emit_status('thinking', ...)` call) are removed. The `try: chunk = json.loads(data_str); yield chunk` body remains functional.
2. No other code path in `hermes_agent.py` references `reasoning_content`, `reasoning`, or emits a `thinking` status.
3. A new regression test in `test_hermes_pipe.py` feeds a `data:` line containing `{"choices":[{"delta":{"reasoning_content":"thinking out loud","content":"hi"}}]}`, collects yielded chunks and emitter events, and asserts:
   - the chunk is yielded intact (or at least without `reasoning_content` being interpreted as `thinking`)
   - no emitter event has `data.sub_action == 'thinking'`
4. The `pipe()` docstring at lines 94-101 is extended (T2) with concrete guidance on when a plain OpenAI Connection suffices vs. when the 4 pipe-only features require using the pipe. The 4 features must be named.
5. After T3, running `env PYTHONPATH=backend uv run pytest backend/open_webui/test/utils/test_hermes_pipe.py backend/open_webui/test/utils/test_file_upload_image_analysis.py backend/open_webui/test/utils/test_builtin_pipes.py backend/open_webui/test/utils/test_hermes_pipes_manifold.py backend/open_webui/test/utils/test_hermes_tool_progress.py -v` shows **18 passed** (up from 17). No existing test regresses.
6. No unused imports introduced; `log` still imported because `log.warning(...)` in the `except` block keeps using it.

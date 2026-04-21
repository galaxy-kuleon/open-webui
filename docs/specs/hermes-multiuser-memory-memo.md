# Execution Spec — Hermes Multi-User Memory via Identity Propagation

**Source**: strategic design conversation 2026-04-21 between Noel Bao and Claude, condensed into an execution-ready memo. All architectural decisions below are user-confirmed.

**Scope contract**: deliver per-user, per-tenant memory for end users of Open WebUI by propagating identity into hermes-agent's existing memory-provider plumbing (self-hosted honcho), then progressively surface the felt effect to the chat UI across 4 waves. We build no new memory system; we wire what hermes already has.

**Repositories**:

- `/Users/noelbao/Works/open-webui` — branch `feat/v0.8.12-hermes-port`, current HEAD at `b626baba4` (skill-intercept refactor committed 2026-04-21)
- `/Users/noelbao/Works/hermes-agent` — our fork. `origin` = kuleon-galaxy fork; `upstream` = `NousResearch/hermes-agent` (user sets before /atw W1)
- `plastic-labs/honcho` — AGPL-3.0 open-source memory backend. **Self-hosted via docker-compose, stable latest release, source NEVER modified**. We consume it only as a black-box vendor via its SDK/HTTP. AGPL does not cascade into our code when honcho is used unmodified as a backend.

**Target stack**: Python 3.11+ backend (uv-managed), SvelteKit frontend (Bun tooling), hermes-agent's existing `MemoryProvider` ABC at `agent/memory_provider.MemoryProvider`.

---

## Non-negotiable invariants

These MUST hold at the end of every wave. Breaking any is a blocker finding and non-deferrable.

1. **No regression in currently-passing tests**. Baselines at run start: `uv run pytest backend/open_webui/test/` = 412 passed / 9 pre-existing failed / 5 pre-existing collection errors (one-above-W5 close after skill-intercept commit `b626baba4`). `bun run check` = 9190 errors (unchanged). `uv run ruff format --check` on all wave-touched files = exit 0. Hermes-agent repo's `uv run pytest` (or equivalent) green delta (no new failures attributable to our edits).
2. **No broken imports anywhere**. `uv run python -c "import open_webui"` succeeds at every wave close. Same for hermes after each hermes-side edit.
3. **Cross-tenant memory isolation is absolute**. Two sessions with different `(tenant_id, user_id)` tuples MUST surface independent honcho peers. A leak — even a partial read — is a critical defect, non-deferrable, and cannot be deferred via `REDIRECT-AND-DESCOPE`.
4. **Honcho server source is vendor-opaque**. Zero modifications to `plastic-labs/honcho`. Our code speaks to it only via the honcho Python SDK (already vendored via hermes's `plugins/memory/honcho/`) or HTTP. If a required feature is missing from honcho, the correct response is to escalate to the user, not patch honcho.
5. **Hermes-agent upstream stays mechanically rebasable**. All our downstream edits to hermes-owned files MUST be bracketed by unique marker comments (`# HERMES-HOOK-<NAME>-BEGIN` / `# HERMES-HOOK-<NAME>-END`). New logic prefers new modules under `hermes_cli/openwebui_adapter/` or similar; touching upstream files is a last resort and registered in the patchmap.
6. **openwebui upstream compat preserved**. Modifications to upstream-originated files are forbidden unless a wave's spec slice explicitly permits. New backend code lives in `backend/open_webui/hermes/`. New frontend code lives in `src/lib/components/hermes/`, `src/lib/hermes/`, or `src/routes/(app)/settings/memory/`. Any unavoidable edit to an upstream file uses `# HERMES-HOOK-*-BEGIN` markers and is registered.
7. **Every behavioural claim is exercised by at least one new or updated test** — unit, integration, or e2e. Tests that only verify "the function exists" do not satisfy this; behaviour means input-output or side-effect assertion.
8. **No destructive actions on shared state**: no DB table drops, no user-data deletion, migrations append-only, no rewrites of existing history.
9. **No opportunistic scope creep**. Findings outside the wave's slice are recorded to the wave's Deferred/Observed section and left alone.
10. **Private fork**: never propose upstream PRs to `open-webui/open-webui` or `NousResearch/hermes-agent`.

---

## Prerequisites (user-owned, validated at /atw T0)

/atw MUST NOT enter `WAVE_START(1)` until all of the following are verified. Kind 3's T0 plan for W1 begins with a prerequisite-check stanza that fails loudly if any item is absent.

1. **Honcho self-hosted reachable**. `curl -sf ${HONCHO_BASE_URL:-http://localhost:8000}/v1/workspaces` returns 200. Container from `plastic-labs/honcho` stable latest tag, started via upstream `docker-compose.yml`, never via a forked image.
2. **hermes-agent `upstream` remote configured**. `git -C /Users/noelbao/Works/hermes-agent remote -v` contains `upstream\thttps://github.com/NousResearch/hermes-agent.git`.
3. **hermes memory setup complete**. `hermes memory status` reports `Provider: honcho`, `Status: available ✓`, `baseUrl` points at the self-hosted instance.
4. **Working tree clean (or carry-documented)**. `git -C /Users/noelbao/Works/open-webui status --short` returns no unexpected uncommitted work outside the known residual set (archive-dir `.md` cosmetic edits, previously-staged e2e/src leftovers from pre-W1 sessions, untracked `.webui_secret_key`). Any other uncommitted work must be committed or documented as carry before W1 starts.

---

## Items

Ordered by blast radius ascending. Each item is standalone — a principal-engineer MUST be able to execute it without cross-reading others.

---

### I-1 (W1) — Identity propagation: openwebui User → hermes memory_manager → honcho peer

**Severity**: enabling (blocks every later wave)
**Blast radius**: medium (two repos touched; zero UI change)

**Files in scope**:

openwebui side:

- `backend/open_webui/hermes/__init__.py` — NEW, package init (empty or docstring only)
- `backend/open_webui/hermes/identity.py` — NEW
  - Function: `resolve_hermes_identity(user) -> dict`
  - Input: openwebui `UserModel` or None
  - Output: `{"user_id": str, "tenant_id": str, "display_name": str, "locale": str}` or `None` if user is None
  - Side effects: one DB query to `Groups.get_groups_by_member_id(user.id)`; no writes
- `backend/open_webui/pipes/hermes_agent.py` — MODIFY (the pipe is our owned code, but edit is marker-bracketed for clarity)
  - At line 88 the signature already receives `__user__: dict | None` but the body never uses it — THIS is the gap we fix
  - Call `resolve_hermes_identity` on `__user__` near the top of `pipe()`
  - Add `X-Hermes-User-Id` and `X-Hermes-Tenant-Id` to `headers` dict built around line 123, gated on identity-resolution success
  - Keep existing `X-Hermes-Session-Id` gating at line 280 unchanged

hermes-agent side:

- `gateway/platforms/api_server.py` — MODIFY, marker-bracketed
  - In `_handle_chat_completions` (line 615): read `X-Hermes-User-Id` and `X-Hermes-Tenant-Id` headers alongside the existing `X-Hermes-Session-Id` extraction (line 672)
  - In `_create_agent` (line 512): accept `user_id: str | None = None` and `tenant_id: str | None = None` params; forward to AIAgent constructor call at line 546
- `run_agent.py::AIAgent.__init__` — MODIFY, marker-bracketed
  - Accept `user_id: str | None = None` and `tenant_id: str | None = None` kwargs
  - Forward into the `memory_manager.initialize_all(session_id=..., **kwargs)` call so the kwargs reach providers; honcho already reads `kwargs.get("user_id")` at `plugins/memory/honcho/__init__.py:293`
- `plugins/memory/honcho/__init__.py` — READ-ONLY. Already reads `user_id` at line 293. If `tenant_id` routing requires honcho workspace scoping and the current provider treats workspace as config-time only, the correct behavioural adaptation in W1 is to encode tenant into `cfg.peer_name` as `{tenant_id}:{user_id}` so different tenants produce different honcho peers even when re-using the same user_id. Do NOT modify this file in W1; document the choice in the wave retro.

shared:

- `docs/hermes-rebase-patchmap.md` — NEW (see I-0 / prerequisite task: skeleton seeded before W1; W1 populates the I-1 rows)
- `scripts/verify_hermes_hooks.sh` — NEW, greps all `HERMES-HOOK-*-BEGIN` markers across both repos; exits non-zero if any registered marker is missing (used during rebase QA; runs locally, not CI-gated in W1)

new test files:

- `backend/open_webui/test/hermes/__init__.py` — NEW
- `backend/open_webui/test/hermes/test_identity.py` — NEW, unit
  - Table-driven: (a) user in group with `meta.tenancy="acme"` → `tenant_id="acme"`, (b) user in group without tenancy meta → `tenant_id=<group.id>`, (c) user with no groups → `tenant_id=<user.id>`, (d) user is None → returns None
- `backend/open_webui/test/pipes/test_hermes_agent_headers.py` — NEW, unit
  - Mock `httpx.AsyncClient.stream` and assert `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` present when `__user__` provided, absent when None
- hermes-agent: `tests/api_server/test_identity_header.py` (or equivalent existing test location) — NEW, unit/integration
  - POST to `/v1/chat/completions` with mocked AIAgent; assert agent received `user_id` kwarg matching the header value
  - Assert absence of header falls back to existing default (no exception, no identity kwargs forwarded)

**Behavioural requirements**:

1. **Pipe identity extraction**. `pipe.pipe(body, __user__={"id": "u1", "name": "Alice", ...}, ...)` calls `resolve_hermes_identity(__user__)` and obtains `{"user_id": "u1", "tenant_id": <resolved>, ...}`.
2. **Tenancy resolution order** (pure function, DB read allowed):
   - First: scan groups via `Groups.get_groups_by_member_id(user_id)`; first group whose `meta.get("tenancy")` is a non-empty string wins; `tenant_id = group.meta["tenancy"]`.
   - Second: if no group has `meta.tenancy`, use the first group's `group.id` as `tenant_id` (pragmatic fallback — a lone group IS effectively the customer).
   - Third: if user belongs to no groups, `tenant_id = user.id` (single-user deployment fallback — preserves isolation even without explicit tenancy configuration).
3. **Headers**. Both `X-Hermes-User-Id: <user_id>` and `X-Hermes-Tenant-Id: <tenant_id>` are set on every HTTP request from pipe to hermes. Absent only when `__user__` is None (e.g. direct-mode testing).
4. **Hermes extraction**. `_handle_chat_completions` reads both headers; non-empty values flow as `user_id` and `tenant_id` kwargs through `_create_agent` → `AIAgent` → `memory_manager.initialize_all`.
5. **Honcho peer scoping**. Honcho provider's `initialize(session_id, user_id=..., tenant_id=..., **)` sets `cfg.peer_name` to `{tenant_id}:{user_id}` when both are present (encoding tenant isolation into the peer namespace). If `cfg.peer_name` was explicitly configured in `honcho.json`, that config wins (existing behaviour preserved).
6. **Fallback symmetry**. Request without `X-Hermes-User-Id` → hermes behaves exactly as pre-W1 (no regression for CLI / gateway / legacy callers).
7. **Cross-tenant isolation proof**. Two successive `/v1/chat/completions` calls with distinct `X-Hermes-User-Id` values create distinct honcho peers. A third call with tenant=A,user=X followed by tenant=B,user=X (same user_id, different tenant) MUST see a fresh empty memory, not A's memory.

**Acceptance criteria**:

1. `uv run pytest backend/open_webui/test/hermes/ backend/open_webui/test/pipes/test_hermes_agent_headers.py` passes.
2. `uv run pytest` on hermes-agent passes with the new `test_identity_header.py`.
3. **Live smoke** (kind 2 must run this, not just accept a passing mock):
   1. Start openwebui + hermes + honcho locally.
   2. Sign in as user A, send "my favourite colour is blue". Wait for response.
   3. Query honcho directly: `curl -sf $HONCHO_BASE_URL/v1/workspaces/<workspace>/peers` — assert one peer exists with name starting `<tenant>:<A.id>`.
   4. Sign in as user B (different tenant or different user_id within same tenant). Send "what is my favourite colour?". Assert response does NOT contain "blue".
   5. Sign in as user A again. Send "what is my favourite colour?". Assert response contains "blue" (via honcho recall).
4. Marker comments registered in `docs/hermes-rebase-patchmap.md`:
   - `HERMES-HOOK-IDENTITY-PIPE` in `backend/open_webui/pipes/hermes_agent.py`
   - `HERMES-HOOK-IDENTITY-API-SERVER` in `hermes-agent/gateway/platforms/api_server.py` (two locations: header extraction + `_create_agent`)
   - `HERMES-HOOK-IDENTITY-AIAGENT` in `hermes-agent/run_agent.py`
5. `scripts/verify_hermes_hooks.sh` exits 0 when all markers present.
6. All invariants hold (see non-negotiable section).

**Non-deferrable**: yes. Without I-1, W2/W3/W4 have no basis.

---

### I-2 (W2) — Memory recall chip in chat UI

**Severity**: feature (first visible wow moment)
**Blast radius**: medium (SSE contract extension + frontend component + minimal chat-message slot)

**Files in scope**:

hermes-agent side:

- `run_agent.py` or appropriate SSE-emission site — MODIFY, marker-bracketed
  - When honcho provider's `prefetch()` returns non-empty text (indicating memory context is being injected this turn), emit a new SSE event: `event: hermes.memory.recalled\ndata: {"peer": "<peer_name>", "context_preview": "<first 200 chars>", "context_token_estimate": <int>}`
  - Event is additive, emitted at turn-start before any `data:` content frames
  - If honcho provider is inactive or prefetch returns empty, no event is emitted (absence = "no recall this turn")

openwebui side:

- `backend/open_webui/pipes/hermes_agent.py` — MODIFY, marker-bracketed
  - Add a third event type handler parallel to `hermes.tool.progress` (already handled at line 220) for `hermes.memory.recalled`
  - Translate to a distinct `__event_emitter__` call with `action="hermes_memory_recall"` (NOT the existing `agent_skill` action — needs a unique frontend-dispatch channel)
- `src/lib/components/hermes/__init__.ts` — NEW (export barrel)
- `src/lib/components/hermes/MemoryChip.svelte` — NEW
  - Props: `peer: string`, `contextPreview: string`, `tokenEstimate: number`
  - Renders: small chip on the message header line with icon + truncated preview (e.g. `← 記得你提過：context_preview[:40]…`). Click expands to full `contextPreview`
  - Dark/light theme matching existing chip styles in the repo
- `src/lib/components/chat/Messages/Message.svelte` or equivalent message-render component — MODIFY, marker-bracketed
  - One slot near the top of the assistant message bubble for `MemoryChip`
  - Gated on a message-level prop populated from the `hermes_memory_recall` event

new test files:

- hermes-agent: `tests/api_server/test_memory_recall_sse.py` — NEW
  - Mock honcho provider to return non-empty `prefetch` → assert SSE stream contains `event: hermes.memory.recalled` before first content frame
  - Mock empty prefetch → assert no recall event emitted
- openwebui: `backend/open_webui/test/pipes/test_hermes_memory_recall_translation.py` — NEW
  - Feed mock SSE stream containing `hermes.memory.recalled` → assert `__event_emitter__` called with `action="hermes_memory_recall"`
- openwebui: `e2e/tests/hermes-memory-chip.spec.ts` — NEW Playwright
  - Two-turn flow: (1) first chat introduces a fact, (2) reload + new chat asks about it, (3) assert MemoryChip appears on the second chat's response

**Behavioural requirements**:

1. When hermes's honcho provider `prefetch()` returns a non-empty context block, hermes emits `hermes.memory.recalled` SSE event with `peer`, `context_preview` (first 200 chars), `context_token_estimate`.
2. openwebui pipe receives this event and emits a distinct `__event_emitter__` status event with `action="hermes_memory_recall"` and the payload.
3. Frontend Svelte component mounted on messages where this event fires renders the chip.
4. Chip is only shown on the assistant message that corresponds to the turn where recall happened (not on historical messages, not on the user message).
5. Chip is absent when honcho is inactive, or when this is user's first turn (no memory to recall).
6. Clicking the chip shows the full recalled context in a collapsible panel (not a modal — keep the interaction lightweight).

**Acceptance criteria**:

1. Unit tests (both repos) green.
2. Playwright test documented above passes against a live openwebui+hermes+honcho stack.
3. Manual UI review: chip styling matches existing `AgentSkillStatus.svelte` visual vocabulary (dark / light theme).
4. Marker comments registered in `hermes-rebase-patchmap.md`.
5. Baseline invariants preserved.

**Non-deferrable**: yes within W2. The chip IS the wow signal; shipping W2 without it is a no-op.

---

### I-3 (W3) — User-facing memory profile panel

**Severity**: feature (trust + compliance foundation)
**Blast radius**: medium (new route + new backend router + proxy integration with hermes's honcho tools)

**Files in scope**:

openwebui side:

- `backend/open_webui/routers/hermes_memory.py` — NEW
  - Endpoints:
    - `GET /api/v1/hermes/memory/profile` → proxies hermes's `honcho_profile` tool; scopes by `current_user.id`
    - `POST /api/v1/hermes/memory/profile` → updates peer card (body: `{"card": [<fact strings>]}`)
    - `GET /api/v1/hermes/memory/search?q=...` → proxies `honcho_search`
    - `GET /api/v1/hermes/memory/context` → proxies `honcho_context`
    - `POST /api/v1/hermes/memory/conclusions` → calls `honcho_conclude` with `conclusion` body
    - `DELETE /api/v1/hermes/memory/conclusions/<conclusion_id>` → calls `honcho_conclude` with `delete_id`
  - All endpoints authenticated (existing openwebui auth middleware); all pass `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` to hermes derived from `current_user` via `resolve_hermes_identity` (from W1)
- `backend/open_webui/main.py` — MODIFY, marker-bracketed
  - Register the new router (one line)
- `src/routes/(app)/settings/memory/+page.svelte` — NEW
  - Lists current peer card facts (editable)
  - Lists conclusions with individual delete buttons
  - Search box over stored context
  - "Export my memory" button (downloads JSON of everything honcho knows)
  - "Delete all my memory" button with double-confirmation (GDPR erasure)
- `src/lib/components/hermes/ProfilePanel.svelte` — NEW (the main panel body; the route shell imports it)
- `src/lib/apis/hermes/memory.ts` — NEW (typed frontend client for the new endpoints)

hermes-agent side:

- Possibly zero changes if hermes's existing honcho tool-call machinery is exposed via the agent API. If hermes does not expose a direct memory-admin HTTP endpoint, we use hermes's existing `handle_tool_call` path via a dedicated API route on hermes's side. W3 T0 plan MUST verify this before dispatching kind 1.

new test files:

- `backend/open_webui/test/routers/test_hermes_memory_router.py` — NEW, unit
  - Each endpoint called → correct hermes tool invoked with correct user scoping → response shape matches OpenAPI contract
- `backend/open_webui/test/routers/test_hermes_memory_tenant_isolation.py` — NEW, unit
  - User A calls `/profile` → sees A's peer card
  - User B calls `/profile` → sees B's peer card
  - Neither can read the other's data even by crafting user_id in the body (router ignores body-specified user_id, uses `current_user.id` only)
- `e2e/tests/hermes-profile-panel.spec.ts` — NEW Playwright
  - Visit `/settings/memory`, add a fact, refresh, verify persistence
  - Delete a fact, verify removal
  - Delete all memory, verify empty state

**Behavioural requirements**:

1. Only authenticated users can reach the endpoints.
2. User identity for hermes is ALWAYS derived from `current_user` on the backend; any user_id in the request body is ignored (defence against IDOR).
3. `GET /profile` returns `{card: [...]}` — the peer card list from honcho.
4. `POST /profile` replaces the card with the new list.
5. Conclusions list + delete work via honcho's `delete_id` mechanism (PII-removal semantics).
6. "Export my memory" returns a complete JSON dump including card, conclusions, and representation.
7. "Delete all my memory" requires typed-confirmation ("delete my memory" literal match) and calls an idempotent hermes endpoint that removes all honcho entries for this peer.

**Acceptance criteria**:

1. Unit + tenant-isolation tests green.
2. Playwright e2e green.
3. Manual check: visit `/settings/memory` in two browsers signed as different users, confirm distinct contents.
4. Marker comments registered.
5. Baselines held.

**Non-deferrable**: yes within W3. Compliance / trust cannot be skipped for enterprise customers.

---

### I-4 (W4) — Proactive continuation detection + UI card

**Severity**: feature (second wow moment: "it did something I didn't teach it")
**Blast radius**: medium-high (hermes-side session-start hook + new homepage slot + SSE event)

**Files in scope**:

hermes-agent side:

- `run_agent.py` or appropriate session-start hook — MODIFY, marker-bracketed
  - On session start (when AIAgent is constructed for `/v1/chat/completions`), BEFORE processing the user message, query honcho via `honcho_reasoning` with a standard prompt: "Did the user leave any task incomplete in their last session? If yes, summarise it in one sentence. If no, respond with [NONE]."
  - Gate: run only if the chat_id is new (first turn of this session) AND honcho provider is active AND last honcho session for this peer is at least 1 hour old (avoid spam on rapid reconnects)
  - If response is not `[NONE]`, emit `event: hermes.continuation.suggested\ndata: {"task_summary": "...", "last_session_age_hours": <int>, "confidence": <float>}` BEFORE processing the user's message
  - Async: don't block first-turn latency; fire-and-forget the reasoning call, emit the event when it returns (may arrive mid-stream — frontend must handle out-of-order)

openwebui side:

- `backend/open_webui/pipes/hermes_agent.py` — MODIFY, marker-bracketed
  - Handle new event type `hermes.continuation.suggested`
  - Translate to `__event_emitter__` with `action="hermes_continuation"`
- `src/lib/components/hermes/ContinueCard.svelte` — NEW
  - Renders on homepage (chat list) when a continuation event is pending
  - "Continue where you left off: <task_summary>" with CTA button "Continue"
  - Click → starts new chat with pre-filled prompt "Please continue: <task_summary>"
- `src/lib/components/hermes/ContinuationChip.svelte` — NEW
  - Variant for mid-chat display (if the continuation event fires during an active chat)
  - Dismissible; dismissal stored in session (one dismissal per event)
- `src/routes/(app)/+page.svelte` or homepage equivalent — MODIFY, marker-bracketed
  - One slot for `ContinueCard` component
- `src/lib/hermes/continuationStore.ts` — NEW (Svelte store for current suggestion state, shared between homepage + chat view)

new test files:

- hermes-agent: `tests/api_server/test_continuation_hook.py` — NEW
  - Mock honcho_reasoning returning a task summary → SSE stream contains `hermes.continuation.suggested`
  - Mock honcho_reasoning returning `[NONE]` → no event emitted
  - Session age < 1 hour → no event (gating test)
- openwebui: `backend/open_webui/test/pipes/test_continuation_translation.py` — NEW
- `e2e/tests/hermes-continuation.spec.ts` — NEW Playwright
  - Multi-session flow: session 1 establishes a task, close + wait (or mock time), session 2 shows ContinueCard

**Behavioural requirements**:

1. Continuation detection runs only at session start (not every turn).
2. Gate: minimum 1-hour gap since last session for this peer.
3. honcho_reasoning timeout bounded (e.g. 8s); timeout → no event, no error.
4. `[NONE]` response → no event.
5. Event payload includes a confidence signal; frontend may hide low-confidence suggestions.
6. Clicking Continue pre-fills a chat input; user can edit before submitting (not auto-submit — explicit action required).
7. Dismissing the card stores dismissal in session so the card doesn't re-appear for this event.

**Acceptance criteria**:

1. Unit + e2e tests green.
2. Manual multi-session walkthrough passes.
3. Latency check: homepage renders without waiting for continuation event (async-injected).
4. Marker comments registered.
5. Baselines held.

**Non-deferrable**: no blocker for product ship — could be descoped to W5 if time-pressured. Unlike I-1/I-2/I-3, W4's absence doesn't break the earlier waves. Kind 3 may classify W4 items as safe-to-defer.

---

## Test-infrastructure contract (every wave)

1. Every new Python test runs under `uv run pytest` (openwebui) or hermes-agent's native test runner.
2. Every new behavioural test includes at least one negative case.
3. Filesystem side-effects isolated via `tmp_path` fixture. No test writes into the real repo or `~/.hermes/` or `~/.claude/`.
4. HTTP-level tests use `httpx.AsyncClient` against the app in-process; no real network to external services.
5. Honcho live-stack tests explicitly marked with a `live_stack` pytest marker; only runs when a configured local honcho is reachable. Skipped otherwise with a clear reason (not a false pass).
6. Playwright e2e tests restore any mutated admin config in `afterEach` block; prerequisite honcho state reset via the W3 "delete my memory" endpoint between tests.
7. Cross-repo integration smoke tests documented in `docs/hermes-smoke-tests.md` — user-runnable locally; not automated until we have a CI environment that can spin up honcho+hermes+openwebui together.

---

## Definition of done for the whole run

- Every item I-1 through I-4 has PASSed kind 2 verification on its closing wave. (Or W4 explicitly descoped + re-planned into a future series.)
- `uv run pytest backend/open_webui/test/` green baseline preserved with every new test added.
- hermes-agent tests green.
- `bun run check` baseline preserved.
- `docs/hermes-rebase-patchmap.md` registers every marker from every wave, with file, line range, marker name, intent, safe-to-drop flag.
- `scripts/verify_hermes_hooks.sh` exits 0 on current HEAD in both repos.
- Each wave commits with a HEREDOC message per `/atw` section 4.4 step 5.
- `.agent-team-waves/wave-1.md` through `wave-4.md` each contain concrete file:line anchors per `/atw` schema.
- User can demonstrate the 4 wow moments to a test customer:
  1. W1 (invisible): memory silently accumulates per user — verifiable via honcho dashboard or `/settings/memory` (W3)
  2. W2 (visible): MemoryChip appears on assistant messages when memory is used
  3. W3 (trust): users can inspect, edit, delete their own memory
  4. W4 (magic): "continue where you left off" card appears on session start after a break

---

## Progressive Disclosure Rules (for /atw kind 7)

This memo is the full spec. Kind 7 reveals ONE wave at a time to kinds 1-6 via `wave-N-reveal.md`. Waves 2, 3, 4 are NOT pre-revealed even though the spec describes them — disclosure follows `/atw`'s oil-painting overlap model.

Decomposition mapping: W1↔I-1, W2↔I-2, W3↔I-3, W4↔I-4 (1:1 by design — no splitting or merging).

Total waves: 4. No `--max-waves` flag required. No decomposition ambiguity (user-confirmed).

## Rebase-friendliness audit (every wave close)

- All hermes-touching changes bracketed by `# HERMES-HOOK-<NAME>-BEGIN` / `# HERMES-HOOK-<NAME>-END` with unique NAME
- `docs/hermes-rebase-patchmap.md` updated
- `scripts/verify_hermes_hooks.sh` exits 0
- For openwebui side: any edit to an upstream-originated file (not `backend/open_webui/hermes/`, `backend/open_webui/pipes/hermes_agent.py`, `src/lib/components/hermes/`, `src/routes/(app)/settings/memory/`) MUST be called out in the wave retro and double-justified

## Scope contract reminder

User confirmed on 2026-04-21:

- Paying customers + their employees are the target persona
- "AI knows me" / "AI remembers" / "AI did it without me asking" are the 3 felt outcomes
- Self-hosted honcho, stable latest release, NOT forked
- Both openwebui and hermes-agent are our forks — rebase hygiene is a first-class constraint
- Memory extraction model is configured in hermes's `config.yaml` (user's decision, NOT openwebui's)
- Hermes's memory adjustment speed is acceptable for inline use
- Skill-intercept WIP committed as `b626baba4` before this run begins

This memo is authoritative. If execution reality forces divergence, update this memo in-repo as the single source of truth before the next reveal packet.

# Hermes Hook Patchmap

Registry of every marker-bracketed edit we make to upstream-originated code across both forks (`open-webui` and `hermes-agent`). Updated at every `/atw` wave close; used as the mechanical rebase aid.

## How to use

1. Every downstream edit to an upstream-originated file MUST be bracketed by `# HERMES-HOOK-<NAME>-BEGIN` / `# HERMES-HOOK-<NAME>-END` (or `// ... //` for TypeScript, `<!-- ... -->` for Svelte markup).
2. Every marker MUST have exactly one row in the relevant table below.
3. After an upstream rebase, run `bash scripts/verify_hermes_hooks.sh`. If any registered marker is missing, the rebase is incomplete — either reapply the hook or update this table (if the hook is no longer needed).
4. New code that lives in our owned directories (`backend/open_webui/hermes/`, `src/lib/components/hermes/`, `hermes_cli/openwebui_adapter/`) does NOT need markers — it's already isolated from upstream surface.

## Marker naming convention

`HERMES-HOOK-<DOMAIN>-<SUBDOMAIN?>` — domain groups related markers (e.g. `IDENTITY`, `MEMORY-RECALL`, `CONTINUATION`). Multiple markers with the same name are allowed if they span logically-related edits across different files within the same wave.

## Status taxonomy

- **load-bearing**: removing or skipping this hook breaks a behavioural contract. Must be reapplied during rebase.
- **cosmetic**: UI-only slot or label; if upstream refactors the render path, the hook can be moved to the new location.
- **optional**: feature enhancement that degrades gracefully without the hook (e.g. a log line).

---

## openwebui fork

Repo: `/Users/noelbao/Works/open-webui`
Upstream: `open-webui/open-webui` (origin: Dev branch)

| Marker | File | Line range | Wave | Intent | Status |
|---|---|---|---|---|---|
| _(none yet — W1 populates)_ | | | | | |

---

## hermes-agent fork

Repo: `/Users/noelbao/Works/hermes-agent`
Upstream: `NousResearch/hermes-agent`

| Marker | File | Line range | Wave | Intent | Status |
|---|---|---|---|---|---|
| _(none yet — W1 populates)_ | | | | | |

---

## Anticipated markers from the memo (pre-registration — W1 confirms actual line ranges at close)

W1 (I-1 — identity propagation):

- `HERMES-HOOK-IDENTITY-PIPE` — openwebui `backend/open_webui/pipes/hermes_agent.py`. Load-bearing. Injects `X-Hermes-User-Id` + `X-Hermes-Tenant-Id` headers derived from `__user__`.
- `HERMES-HOOK-IDENTITY-API-SERVER-HEADER` — hermes `gateway/platforms/api_server.py` in `_handle_chat_completions`. Load-bearing. Extracts incoming headers.
- `HERMES-HOOK-IDENTITY-API-SERVER-AGENT` — hermes `gateway/platforms/api_server.py` in `_create_agent`. Load-bearing. Accepts new kwargs.
- `HERMES-HOOK-IDENTITY-AIAGENT` — hermes `run_agent.py` in `AIAgent.__init__`. Load-bearing. Threads kwargs to memory_manager.

W2 (I-2 — memory recall chip):

- `HERMES-HOOK-MEMORY-RECALL-SSE` — hermes `run_agent.py` or SSE-emission site. Load-bearing. Emits `hermes.memory.recalled` event.
- `HERMES-HOOK-MEMORY-RECALL-PIPE` — openwebui `backend/open_webui/pipes/hermes_agent.py`. Load-bearing. Translates SSE to `__event_emitter__`.
- `HERMES-HOOK-MEMORY-RECALL-UI` — openwebui `src/lib/components/chat/Messages/Message.svelte` (or whatever the message-render path is). Cosmetic. Chip slot.

W3 (I-3 — profile panel):

- `HERMES-HOOK-MEMORY-ROUTER-REGISTER` — openwebui `backend/open_webui/main.py`. Load-bearing. Registers the new memory router.

W4 (I-4 — continuation):

- `HERMES-HOOK-CONTINUATION-HOOK` — hermes `run_agent.py` session-start path. Load-bearing (for this wave's feature).
- `HERMES-HOOK-CONTINUATION-PIPE` — openwebui pipe. Load-bearing.
- `HERMES-HOOK-CONTINUATION-HOMEPAGE` — openwebui `src/routes/(app)/+page.svelte` (or homepage equivalent). Cosmetic.

---

## Rebase protocol

1. Before rebasing either fork against its upstream:
   - Commit or stash any uncommitted work.
   - Record current HEAD: `git rev-parse HEAD` on both repos.
2. `git fetch upstream && git rebase upstream/<branch>` (or `merge` if preferred).
3. Resolve conflicts. Prefer keeping our marker-bracketed regions; reject upstream changes that conflict inside a marker block unless the intent is preserved.
4. After rebase: `bash scripts/verify_hermes_hooks.sh`.
   - Exit 0 → all registered markers still present; proceed to test suite.
   - Exit non-zero → script names the missing markers. Either reapply the hook (if the upstream change removed it) or update this table (if the marker is no longer needed).
5. Run full test suites on both repos. If any test related to a marker fails, reapplication was incomplete.
6. Commit rebase result with message: `rebase: pull upstream <date> — markers verified (<count> present)`.

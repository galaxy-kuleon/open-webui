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
| HERMES-HOOK-IDENTITY-PIPE | `backend/open_webui/pipes/hermes_agent.py` | 124-126, 133-137 | 1 | Inject X-Hermes-User-Id + X-Hermes-Tenant-Id headers when resolve_hermes_identity(__user__) returns non-None | load-bearing |
| HERMES-HOOK-MEMORY-RECALL-PIPE | `backend/open_webui/pipes/hermes_agent.py` | 240-251 | 2 | Translate `event: hermes.memory.recalled` SSE frame into `__event_emitter__` status event with `action="hermes_memory_recall"` for downstream UI | load-bearing |
| HERMES-HOOK-MEMORY-RECALL-UI | `src/lib/components/chat/Messages/ResponseMessage/StatusHistory/StatusItem.svelte` | import block + `{:else if status?.action === 'hermes_memory_recall'}` branch | 2b | Mount HermesMemoryRecallStatus component when status action matches the memory-recall dispatch from pipe | cosmetic |

---

## hermes-agent fork

Repo: `/Users/noelbao/Works/hermes-agent`
Upstream: `NousResearch/hermes-agent`

| Marker | File | Line range | Wave | Intent | Status |
|---|---|---|---|---|---|
| HERMES-HOOK-IDENTITY-API-SERVER-HEADER | `gateway/platforms/api_server.py` | 817-825 | 1 | Extract X-Hermes-User-Id + X-Hermes-Tenant-Id from _handle_chat_completions request headers, strip whitespace, treat empty-after-strip as absent | load-bearing |
| HERMES-HOOK-IDENTITY-API-SERVER-AGENT | `gateway/platforms/api_server.py` | 614-623 | 1 | Accept user_id/tenant_id kwargs in _create_agent; build identity_kwargs dict omitting falsy values; forward via **identity_kwargs to AIAgent constructor | load-bearing |
| HERMES-HOOK-IDENTITY-AIAGENT | `run_agent.py` | 1417-1422 | 1 | AIAgent.__init__ accepts tenant_id (user_id existed pre-wave); populate _init_kwargs["tenant_id"] when self._tenant_id is truthy, forwarded into memory_manager.initialize_all(**_init_kwargs) | load-bearing |
| HERMES-HOOK-MEMORY-RECALL-SSE | `run_agent.py` | 9695-9703 | 2 | After prefetch_all(), invoke self.memory_recall_callback when non-empty; callback param added to AIAgent.__init__ around 599 + assigned around 799 | load-bearing |
| HERMES-HOOK-MEMORY-RECALL-SSE | `gateway/platforms/api_server.py` | 886-908 | 2 | `_on_memory_recall` closure + `__memory_recall__` tag in `_emit` closure; callback threaded through `_create_agent` and `_run_agent` to AIAgent; emits `event: hermes.memory.recalled\ndata: {...}` SSE frame before content frames | load-bearing |

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

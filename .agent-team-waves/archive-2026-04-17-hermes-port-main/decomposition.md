# Wave Decomposition

Total waves: 4
Mode: auto
Max waves flag: none

## Wave 1: Make non-vision image upload conditional on actual image-analysis config availability

- Data-flow segment: entry (upload gating decision)
- Blast radius: smallest-isolated (single component conditional + config read)
- Spec sections this wave receives: Remediation WS2 (remaining frontend gate), Handoff §1 (remaining P0 slice), Handoff §Important Limitation
- Planned status: pending
- Deferred items assigned here: None

### Turn Plan

| Turn | Primary Scope                                                                                                   | Revisit          | Lens               | Notes                                            |
| ---- | --------------------------------------------------------------------------------------------------------------- | ---------------- | ------------------ | ------------------------------------------------ |
| T1   | Investigate config/store surface for IMAGE_ANALYSIS_ENABLED; implement config-aware gate in MessageInput.svelte | — (first stroke) | —                  | Must find how frontend accesses backend config   |
| T2   | Edge case hardening: vision model still works, disabled config blocks correctly, error messaging                | T1               | contract-alignment | Verify frontend gate ↔ backend dispatch contract |
| T3   | Full cross-validation of image upload path end-to-end                                                           | T1, T2           | global-consistency | Wave coherence pass                              |

## Wave 2: Reconnect agent skill ZIP import UI + fix terminal selection persistence regression

- Data-flow segment: entry + transform (file import path + state persistence)
- Blast radius: small (two isolated UI components, no interaction between them)
- Spec sections this wave receives: Remediation WS4 (ZIP import), Remediation WS6 (terminal persistence)
- Planned status: pending
- Deferred items assigned here: None

### Turn Plan

| Turn | Primary Scope                                                                                            | Revisit          | Lens               | Notes                                          |
| ---- | -------------------------------------------------------------------------------------------------------- | ---------------- | ------------------ | ---------------------------------------------- |
| T1   | ZIP import: add .zip to Skills.svelte accept list, wire handler to uploadSkillZip, refresh after import  | — (first stroke) | —                  | Backend route and API helper already exist     |
| T2   | Terminal persistence: investigate intended v0.8.12 behavior, fix Chat.svelte ↔ layout.svelte consistency | T1               | shared-patterns    | Both are UI state-wiring fixes; check patterns |
| T3   | Full cross-validation of both import and persistence paths                                               | T1, T2           | global-consistency | Wave coherence pass                            |

## Wave 3: Restore RAG admin settings UI parity with A

- Data-flow segment: transform (admin config round-trip: UI → API → persistence → reload)
- Blast radius: moderate (admin settings page + API payloads + 9+ config values)
- Spec sections this wave receives: Remediation WS3 (RAG settings parity)
- Planned status: pending
- Deferred items assigned here: None

### Turn Plan

| Turn | Primary Scope                                                                                   | Revisit          | Lens               | Notes                                                                                        |
| ---- | ----------------------------------------------------------------------------------------------- | ---------------- | ------------------ | -------------------------------------------------------------------------------------------- |
| T1   | Add missing RAG settings controls to Documents.svelte; fix embedding prefix payload bug         | — (first stroke) | —                  | 9+ settings: prefixes, full doc context, subchat concurrency, doc index gen, user collection |
| T2   | Round-trip verification: load values from backend on page load, save persists, reload preserves | T1               | contract-alignment | UI ↔ retrieval API ↔ config persistence contract                                             |
| T3   | Full cross-validation: audit for any remaining A-era settings not surfaced                      | T1, T2           | global-consistency | Wave coherence pass                                                                          |

## Wave 4: Lockfile/packaging consistency + Hermes integration coverage hardening

- Data-flow segment: output + error-path (build toolchain + test surface)
- Blast radius: moderate-to-system-wide (dependency graph + new test files)
- Spec sections this wave receives: Remediation WS7 (lockfile), Remediation WS8 (Hermes coverage), Verification Matrix, Definition of Done
- Planned status: pending
- Deferred items assigned here: None

### Turn Plan

| Turn | Primary Scope                                                                                                           | Revisit          | Lens               | Notes                                              |
| ---- | ----------------------------------------------------------------------------------------------------------------------- | ---------------- | ------------------ | -------------------------------------------------- |
| T1   | Lockfile regeneration: audit package.json, regenerate package-lock.json, verify fresh install leaves no diff            | — (first stroke) | —                  | Mechanical but system-wide blast radius            |
| T2   | Hermes coverage: builtin pipe bootstrap tests, manifold discovery, SSE event translation, auth-gated session continuity | T1               | contract-alignment | Verify test contracts match actual Hermes behavior |
| T3   | Full cross-validation + Definition of Done checklist against remediation plan                                           | T1, T2           | global-consistency | Final wave coherence + DoD verification            |

## Deferred Queue

- None

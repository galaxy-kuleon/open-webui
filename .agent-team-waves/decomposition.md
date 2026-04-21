# Wave Decomposition

Total waves: 4
Mode: auto
Max waves flag: none

**Source spec**: `docs/specs/hermes-multiuser-memory-memo.md` (committed `3917d4cad`, amended `6ed9d0cda`)

**Mapping**: Items I-1..I-4 from the memo map 1:1 to waves. No splitting, no merging, no inserted remediation waves (yet).

---

## Wave 1: Identity propagation (openwebui User → hermes memory_manager kwargs → memory provider)

- Data-flow segment: entry (request ingress + header wiring)
- Blast radius: smallest-bounded (2 new files openwebui, 3 marker-bracketed edits hermes, 1 patchmap row set)
- Spec sections this wave receives: I-1 in full; invariants; prerequisites; test-infrastructure contract; Amendment (holographic Tier A/B split)
- Planned status: pending
- Deferred items assigned here: None

## Wave 2: Memory recall chip in chat UI

- Data-flow segment: transform (hermes emits new SSE event → pipe translates → Svelte renders chip)
- Blast radius: medium (new SSE event type on hermes side, pipe event-type branch, new Svelte component + minimal message-slot injection)
- Spec sections this wave receives: I-2; invariants; test-infrastructure contract; Amendment acknowledging holographic may not produce rich recall signal — chip can render on fact-retrieval hits only
- Planned status: pending
- Deferred items assigned here: None (pre-plan)

## Wave 3: User-facing memory profile panel

- Data-flow segment: output (user-facing management endpoints + settings page)
- Blast radius: medium (new backend router, new Svelte route, proxy to hermes tool-call path for fact_store/fact_feedback)
- Spec sections this wave receives: I-3; invariants; test-infrastructure contract
- Planned status: pending
- Deferred items assigned here: None (pre-plan)

## Wave 4: Proactive continuation

- Data-flow segment: transform (session-start hook in hermes → SSE event → pipe → homepage + chip)
- Blast radius: medium-high (hermes hook touches session-start path; frontend adds homepage slot + Svelte store)
- Spec sections this wave receives: I-4; invariants; test-infrastructure contract
- Planned status: pending
- Deferred items assigned here: None (pre-plan)

---

## Deferred Queue

- None at start of run. Populated by kind 7 when a wave closes `COMPLETE_WITH_CARRY` or when a non-deferrable finding must be re-assigned.

## Known out-of-scope for this run (from memo, do NOT pick up opportunistically)

- Honcho self-host integration (memo Amendment: deferred to a future provider-upgrade wave if W2 UX requires it).
- Per-user SQLite scoping patch to `holographic` (same deferral; memo Amendment option P2).
- Cross-cutting ruff / lint cleanup outside wave-touched files.
- Any upstream PR preparation (private forks).

## Progressive-disclosure reminder

Kind 7 reveals ONE wave at a time via `wave-N-reveal.md`. Kinds 1–6 never see future-wave slices. This decomposition is kind-7-only context; do not paste into reveal packets.

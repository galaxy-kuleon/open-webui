# Wave Decomposition

Total waves: 5
Mode: auto
Max waves flag: none
Spec: `.agent-team-waves/fix-review-findings-spec.md`
Decomposition heuristic: data-flow cut × blast-radius ascending
Decomposed: 2026-04-18

## Wave 1: dead code removal + admin UI gap

- Data-flow segment: output (UI gap) + isolated-removal (dead code)
- Blast radius: smallest-isolated
- Spec sections this wave receives: F-1 (H-4), F-2 (H-6)
- Planned status: pending
- Deferred items assigned here: None

Rationale: deleting `utils/research.py` before any later wave removes a phantom caller list that would otherwise complicate F-8's user-threading refactor. The single-Switch UI gap is the cheapest blocker in the run and opens the Playwright path used by later waves.

## Wave 2: entry-boundary hardening

- Data-flow segment: entry / upload
- Blast radius: small-bounded (one router + one config endpoint + one loader)
- Spec sections this wave receives: F-3 (C-1), F-4 (C-4), F-5 (H-3)
- Planned status: pending
- Deferred items assigned here: None

Rationale: all three fixes sit at the "user/admin → server" boundary and do not cross into middleware or LLM orchestration. Hardening entry before transform means the later waves operate on already-validated inputs.

## Wave 3: skill execution wiring + prompt-injection hardening

- Data-flow segment: transform (LLM-facing context assembly)
- Blast radius: medium
- Spec sections this wave receives: F-6 (C-2), F-7 (C-3)
- Planned status: pending
- Deferred items assigned here: None

Rationale: both findings modify how user-controlled data reaches the LLM (tool registration + system-prompt injection). Grouped to keep the "what flows into the model" mental model coherent for the cross-validation turn.

## Wave 4: identity threading + testable refactor

- Data-flow segment: transform (credential propagation) + test-infrastructure
- Blast radius: medium-cross-module
- Spec sections this wave receives: F-8 (H-1), F-9 (H-5)
- Planned status: pending
- Deferred items assigned here: None

Rationale: F-8 (user threading through `_async_llm_completion`) and F-9 (extract skip_rag block for testability) both touch `middleware.py` + adjacent utils, and F-9's extraction makes F-8's test easier to write. After W1 deletes `utils/research.py`, F-8 and F-9 have a strictly smaller surface. Grouped.

## Wave 5: resource boundaries + silent-failure

- Data-flow segment: error-path / resource-management
- Blast radius: largest in this run (DB session lifecycle + cascade fallback)
- Spec sections this wave receives: F-10 (H-2), F-11 (H-7)
- Planned status: pending
- Deferred items assigned here: None

Rationale: both findings are about failure-handling correctness in already-shipped code paths and require independent behavioural verification. Placed last so all prior waves' entry/transform invariants are in place before adjusting how the system _degrades_ under stress.

## Deferred Queue

- None (this is the initial decomposition; re-plan will record any carry items at wave close)

## Global constraints carried into every wave

- Scope contract: Critical + High from the 2026-04-18 forensic review ONLY. Medium/Low/Nit/Architecture findings from that review are NOT to be fixed in this run.
- Private fork: never propose upstream PRs.
- No destructive migrations.
- Every behavioural claim must be exercised by at least one new or updated test.
- Wave commits use HEREDOC format per `/atw` section 4.4 step 5. No amends. No force pushes.

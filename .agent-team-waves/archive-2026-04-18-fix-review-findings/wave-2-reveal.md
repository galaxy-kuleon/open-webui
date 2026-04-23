# Wave 2 - Reveal Packet

**Wave Objective**: harden three entry-boundary surfaces — (a) the skill-ZIP upload pipeline against zip-slip / symlink / absolute-path escape; (b) the KG1 OCR subprocess reader against OOM via unbounded `readline` AND the admin-settable project-dir against arbitrary filesystem roots; (c) the admin-settable `RAG_KNOWLEDGE_EXPORT_DIR` against non-validated paths.
**Data-flow segment**: entry / upload
**Blast radius**: small-bounded (one router + one loader + one config setter)
**Total waves**: 2 of 5

## Spec Slice (from fix-review-findings-spec.md)

---

### F-3 (C-1) — Zip-slip + symlink escape in `upload_skill_zip`

**Severity**: Critical
**Blast radius**: small-bounded (single router + helper)
**Files in scope**:

- `backend/open_webui/routers/skills.py:upload_skill_zip` (lines ~273-327 at review time; re-verify at execution)
- New test fixtures under `backend/open_webui/test/fixtures/skills/` (create dir if missing):
  - `malicious-traversal.zip` (contains `../../etc/passwd` entry)
  - `malicious-symlink.zip` (contains a symlink to `/etc/passwd`)
  - `malicious-absolute.zip` (contains an absolute path entry)
- `backend/open_webui/test/routers/test_skills_upload.py` (CREATE or extend) — exercises all three fixtures against a TestClient

**Threat model**: any user with `workspace.skills` permission can upload a ZIP whose members escape the intended sandbox.

**Behavioural requirement**: ZIP uploads with unsafe members return HTTP 400 with a deterministic error string; no file is written anywhere outside the intended extraction prefix; subsequent `view_skill` / `run_agent_skill` cannot reach files outside the allowed root.

**Technical constraints**:

1. Iterate `zf.infolist()` **before** `extractall` and reject any entry where:
   - `Path(name).is_absolute()` is true, OR
   - `'..' in Path(name).parts`, OR
   - member is a symlink: `((info.external_attr >> 16) & 0o170000) == 0o120000`, OR
   - member is a hardlink (same external_attr test for `0o010000`)
2. Pass `filter='data'` explicitly to `extractall` for Python 3.12+ safe-members semantics.
3. After extraction, walk `skill_root` with `os.walk(followlinks=False)` and reject if any `path.is_symlink()` remains.
4. The `shutil.copytree(skill_root, persistent_dir, dirs_exist_ok=True)` call MUST pass `symlinks=False` and use an `ignore` callable that rejects symlinks explicitly.
5. Error responses are HTTP 400 with body `{"detail": "Unsafe path in zip"}` or `{"detail": "Symlinks not permitted"}`. Do not leak file paths or internal extraction state.
6. The uncompressed-size cap already in place stays; do not widen it.

**Acceptance criteria**:

- The three new fixtures each cause HTTP 400 when uploaded.
- Happy-path upload (existing `e2e/tests/skill-zip-import.spec.ts`) still passes unchanged.
- No file under `~/.claude/skills/` or `/tmp/` exists after any rejected upload (verify with `os.listdir`).
- `uv run pytest backend/open_webui/test/routers/test_skills_upload.py` green.

**Non-deferrable**: yes. Critical — arbitrary-file-write class.

---

### F-4 (C-4) — KG1 OCR: unbounded `readline` + admin-provided `project_dir` not validated

**Severity**: Critical
**Blast radius**: bounded (one loader + one config-setter path)
**Files in scope**:

- `backend/open_webui/retrieval/loaders/kg1.py` (lines ~447-501 and ~474 for `_drain*` helpers)
- `backend/open_webui/routers/retrieval.py::update_rag_config` (where `KG1_GLMOCR_PROJECT_DIR` is accepted)
- A unit test for the drain-reader with a malicious stream (1 MB line without newline)

**Behavioural requirements**:

1. The `_drain` / `_drain_soffice` subprocess-output reader MUST be bounded: replace unbounded `readline()` with a read-until-`\n`-or-size-cap pattern (cap: 64 KiB per line; truncate overflow with a log line, do NOT raise).
2. `KG1_GLMOCR_PROJECT_DIR` MUST be validated at config-write time:
   - Path must exist, be a directory, be readable by the server process, and resolve under an allowlist base (default: `~/.kg1-ocr` or `os.environ.get('KG1_PROJECT_ROOT')`). Reject anything else with HTTP 400.
   - If the env var / default is unset, require an **explicit** `KG1_GLMOCR_PROJECT_DIR` set in settings — do NOT fall back to an arbitrary default path.
3. The existing `subprocess.Popen(..., cwd=..., env=...)` contract does not need shell quoting changes (argv mode is already safe), but add a comment documenting the trust boundary.

**Acceptance criteria**:

- Malicious-stream unit test: feed `b'A' * (10 * 1024 * 1024) + b'\n'` into the drain reader and assert wall-clock < 1s + RSS delta < 100 MiB + returned-line length ≤ 64 KiB.
- `update_rag_config` rejects `KG1_GLMOCR_PROJECT_DIR=/etc` with HTTP 400; accepts a path under the allowlist base.
- Happy-path KG1 OCR (if test fixtures exist) continues to succeed.

**Non-deferrable**: yes (Critical — DoS-class via OOM).

---

### F-5 (H-3) — `RAG_KNOWLEDGE_EXPORT_DIR` accepted without validation

**Severity**: High
**Blast radius**: small (one config-setter path)
**Files in scope**:

- `backend/open_webui/routers/retrieval.py::update_rag_config` (where `RAG_KNOWLEDGE_EXPORT_DIR` is accepted)
- `backend/open_webui/utils/knowledge_export.py` consumers (no change expected; contract preserved)

**Behavioural requirement**: admin cannot set `RAG_KNOWLEDGE_EXPORT_DIR` to a system-sensitive or non-writable path.

**Technical constraints**:

- At config-write time, resolve the path, then verify:
  - `Path.is_dir()` returns true (create if missing is acceptable, but reject `Path("/etc")` / `Path("/")` / `Path("/proc")` / any path that already exists and is not a dir).
  - `os.access(path, os.W_OK)` returns true.
  - The resolved path is under an allowlist base: default `~/.open-webui/knowledge-export`, overridable by `RAG_KNOWLEDGE_EXPORT_ROOT` env var.
  - Reject absolute paths outside the allowlist with HTTP 400.
- If the input is an empty string or None, fall back to the allowlist-base default and create it if missing.

**Acceptance criteria**:

- `PUT /api/retrieval/config` with `RAG_KNOWLEDGE_EXPORT_DIR=/etc` returns HTTP 400.
- With a valid path, the round-trip persists and `export_document_files` continues to write files as before.
- No existing e2e test breaks.

**Non-deferrable**: yes.

---

## Deferred Items Assigned To This Wave

None.

## Constraints for This Wave

- **Allowed files to modify**:
  - `backend/open_webui/routers/skills.py` (F-3 extraction hardening only)
  - `backend/open_webui/retrieval/loaders/kg1.py` (F-4 drain reader + trust-boundary comment only)
  - `backend/open_webui/routers/retrieval.py` (F-4 + F-5 config-validation only — do NOT touch other RAG fields or the prefix-sync block)
  - `backend/open_webui/test/fixtures/skills/` (CREATE; new ZIP fixtures for F-3)
  - `backend/open_webui/test/routers/test_skills_upload.py` (CREATE or extend; F-3 tests)
  - `backend/open_webui/test/retrieval/loaders/test_kg1_drain.py` or similar (CREATE; F-4 drain-reader unit test)
  - `backend/open_webui/test/routers/test_retrieval_config.py` or similar (CREATE or extend; F-4 + F-5 config-validation tests)
- **Forbidden files**:
  - Everything under `.agent-team-waves/archive-*/`
  - `backend/open_webui/utils/middleware.py` (W3/W4)
  - `backend/open_webui/utils/tools.py` (W3)
  - `backend/open_webui/utils/sanitize.py` (W3 — prompt-injection helper lives there)
  - `backend/open_webui/utils/image_analysis.py` / `utils/knowledge_export.py` (W4; `utils/knowledge_export.py` is READ-ONLY here — we only need to preserve its contract from F-5's POV, not edit it)
  - `src/lib/components/admin/Settings/Documents.svelte` (W1 only; no UI changes expected in W2)
  - The 4 F-3 through F-11 Python files outside this wave's three targets
- **Out-of-scope items (deferred to future waves)**:
  - F-6, F-7 → Wave 3
  - F-8, F-9 → Wave 4
  - F-10, F-11 → Wave 5
  - `RAG_RESEARCH_MODEL` orphan PersistentConfig (observation only; not fixed in this run)
  - All Medium/Low/Nit/Architecture findings from the review

## Handoff from Wave 1

Wave 1 closed COMPLETE (commit `a84b62381`, T1 + T2 + 1 retry + T3). Pertinent state:

- `utils/research.py` and its 3 test replicas are deleted — imports / symbol references confirmed zero across `backend/` and `src/`.
- `RAG_USER_COLLECTION_ENABLED` admin Switch is present in `Documents.svelte` with `data-testid="rag-user-collection-enabled-switch"`; Playwright spec `e2e/tests/admin-rag-settings.spec.ts` has 2 tests (UI toggle + 10-control API round-trip) — type-clean under strict TS.
- Baselines established at end of Wave 1: `pytest backend/open_webui/test/` = 222 passed / 9 pre-existing failed / 5 pre-existing collection errors; `bun run check` = 9190 errors (all pre-existing in `src/`).
- No carry items. Kind 3 issued only PROCEED AS PLANNED directives; recovery ladder untouched.
- **Deferred observation** (NOT a blocker for W2): `RAG_RESEARCH_MODEL` PersistentConfig at `config.py:4197` is now orphan — surfaced in admin UI but no backend consumer since W1's deletion. Out of scope for this run.

W2 operates on disjoint files from W1 — no revisit coupling expected except the "admin-UI config-update round-trip" surface, where W2's new validation for `KG1_GLMOCR_PROJECT_DIR` and `RAG_KNOWLEDGE_EXPORT_DIR` must NOT regress the Playwright spec that W1 added. Verify with a re-run of the spec's static-analysis pass at T3.

## Success Criteria

1. F-3: three malicious-ZIP fixtures each return HTTP 400 via the pytest TestClient. Happy-path upload continues to succeed. No file written outside the intended extraction prefix after any rejected upload.
2. F-4a (readline): unit test feeds a 10 MiB no-newline stream into the drain reader; wall-clock < 1s, returned line ≤ 64 KiB, no OOM.
3. F-4b (project_dir): `PUT /api/retrieval/config` with `KG1_GLMOCR_PROJECT_DIR=/etc` returns HTTP 400; accepts a valid allowlisted path.
4. F-5: `PUT /api/retrieval/config` with `RAG_KNOWLEDGE_EXPORT_DIR=/etc` returns HTTP 400; with a valid path the round-trip persists.
5. `uv run pytest backend/open_webui/test/` — the pre-existing 9 failures + 5 collection errors remain exactly pre-existing (zero new failures). The new W2 tests all green.
6. `uv run python -c "import open_webui"` exit 0.
7. `bun run check` error count ≤ 9190 baseline (no frontend regression; W2 is backend-only).
8. `ruff format` (or the project's Python formatter, if configured) on all W2-touched Python files produces zero diff.
9. No forbidden file modified — `git diff --name-only` against wave-start confined to the allowed list.
10. No out-of-scope finding touched.

## Notes for principal-engineer (kind 1)

- F-3 reject-before-extract is non-negotiable — do NOT rely on `extractall(filter='data')` alone (that's Python 3.12+ semantics; the project's `pyproject.toml` may support 3.11). Both layers are required.
- F-4 `_drain` reader: the test must PROVE bounded memory/time. `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` delta is the right measure on Linux/macOS.
- F-5: the allowlist base must be resolvable via `Path.resolve()` against the provided input — symlink-trick escapes must fail. Use `Path(input).resolve().is_relative_to(base.resolve())` (Python 3.9+).
- Do NOT widen any existing size cap (F-3 uncompressed cap stays). Do NOT change the subprocess argv contract.
- Add short comments at the trust boundaries (1 line each, per CLAUDE.md guidance — no essays).
- Kind 4 may build the ZIP fixtures (mechanical), but the validation logic + test scaffolding are kind 1 judgement work.

## Notes for evaluator (kind 2)

- For F-3: build your own malicious ZIP under `/tmp/` to verify kind 1's guards aren't only catching the exact fixtures they ship. Vary the attacker shape: nested `../`, URL-encoded paths, mixed forward/back slashes on case-insensitive filesystems.
- For F-4: measure BOTH time and RSS. Kind 1's acceptance criterion is `< 1s + < 100 MiB`; verify with your own `time`/`resource` wrapper.
- For F-5: try a symlink-trick: `mkdir -p /tmp/evil && ln -s /etc /tmp/evil/link` and attempt to set `RAG_KNOWLEDGE_EXPORT_DIR=/tmp/evil/link`. Expected: HTTP 400. Test this independently; don't accept kind 1's claim.
- T3 revisit MUST independently re-run W1's pytest + Playwright TS-strict check to confirm zero regression from W1 baselines.

Revisit scopes and lenses will be specified by kind 3 in the T0 plan.

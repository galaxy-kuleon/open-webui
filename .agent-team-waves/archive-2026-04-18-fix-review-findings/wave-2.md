# Wave 2 - Retrospective

**Status**: COMPLETE
**Wave Objective**: Harden three entry-boundary surfaces — F-3 (C-1) skill-ZIP upload against zip-slip/symlink/traversal; F-4 (C-4) KG1 OCR drain reader against OOM + `KG1_GLMOCR_PROJECT_DIR` validation; F-5 (H-3) `RAG_KNOWLEDGE_EXPORT_DIR` validation
**Turns executed**: 3 (of budget 3), plus 1 retry on T2 (of budget 5)
**Master directives issued**: 6
**Junior dispatches**: kind 4=0, kind 5=0
**Date**: 2026-04-18

## Turn Log

### T1

- **Master directive for this turn**: T1 = F-3 only (zipslip hardening + malicious ZIP fixtures + test file). Split from F-4/F-5 to keep commit units separable.
- **Principal work**:
  - `backend/open_webui/routers/skills.py`: added `_is_unsafe_zip_member(info) -> (unsafe, detail)` with symlink external_attr mask `((external_attr >> 16) & 0o170000) == 0o120000`; added `_assert_no_symlinks_in_tree(root)` using `os.walk(followlinks=False)`; replaced `zf.extractall()` with per-member `zf.extract(info, path)` after pre-walk rejection; `shutil.copytree(..., symlinks=False, dirs_exist_ok=True)` on staging→persistent move; sanitized 400 error strings (`"Unsafe path in zip"`, `"Symlinks not permitted"`, `"Hardlinks not permitted"`) — no user-input echo, no path leak. Noted Python 3.12's `filter='data'` is tarfile-only per PEP 706 (not applicable here).
  - `backend/open_webui/test/routers/__init__.py`: new (package init)
  - `backend/open_webui/test/routers/test_skills_upload.py`: new — 22 tests (13 unit + 5 integration + 1 happy-path + 3 parametric)
  - `backend/open_webui/test/fixtures/__init__.py`: new (package init)
  - `backend/open_webui/test/fixtures/skills/__init__.py`: new (package init)
  - `backend/open_webui/test/fixtures/skills/_build_fixtures.py`: new — committable audit script with in-file `_make_*` builders; disk ZIPs gitignored
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 244 passed (baseline 222 + 22 new)
  - 12 independent adversarial ZIP probes: nested traversal, dot-slash, mixed bad+good, deep symlinks, Windows drive, empty ZIP — all rejected 400
  - No file leak post-rejection verified
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/routers/skills.py:446-451`: pre-existing `ERROR_MESSAGES.DEFAULT(str(e))` pattern leaks `str(e)` in generic exception handler — out of scope for W2, noted as future cleanup candidate
  - Generated ZIP files in fixtures need gitignore — flagged for T2 to close
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/skills.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/__init__.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_skills_upload.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/__init__.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/skills/__init__.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/skills/_build_fixtures.py`

### T2

- **Master directive for this turn**: F-4 + F-5 via shared `_validate_admin_dir` helper in `routers/retrieval.py`; revisit T1 under `shared-patterns` lens. Mode B policy clarification issued before execution: truncate + visible marker + WARN log + continue (NO raise); marker format exact: `...[drain-truncated: <N> bytes omitted]`.
- **Principal work**:
  - `backend/open_webui/routers/retrieval.py:842`: new `_validate_admin_dir(value, *, allowed_roots, setting_name) -> str` — module-private helper, 7-step validation chain (empty/whitespace → null byte/`..` → `realpath` → trailing-sep containment → existence → directory-check); detail strings `"<setting_name>: <reason-class>"` with no raw input echo
  - `backend/open_webui/routers/retrieval.py:805`: new `_kg1_allowed_roots()` — defaults to `~/.kg1-ocr`, env override `KG1_GLMOCR_ALLOWED_ROOT` with `:`-split
  - `backend/open_webui/routers/retrieval.py:824`: new `_rag_export_allowed_roots()` — defaults to `~/.open-webui/knowledge-export`, env override `RAG_KNOWLEDGE_EXPORT_ROOT`
  - `backend/open_webui/routers/retrieval.py:962`: F-5 call site
  - `backend/open_webui/routers/retrieval.py:1152`: F-4b call site
  - `backend/open_webui/retrieval/loaders/kg1.py:28`: new `_read_bounded_line(stream, *, identity)` with `_DRAIN_LINE_CAP = 64 * 1024`; marker `...[drain-truncated: <N> bytes omitted]`; both `_drain_soffice` and `_drain` replaced
  - `backend/open_webui/test/routers/test_validate_admin_dir.py`: new — 19 tests
  - `backend/open_webui/test/routers/test_retrieval_config.py`: new — 7 tests
  - `backend/open_webui/test/retrieval/__init__.py`: new (package init)
  - `backend/open_webui/test/retrieval/test_kg1_drain.py`: new — 15 tests
  - `.gitignore`: added `backend/open_webui/test/fixtures/skills/*.zip` (closed T1 hygiene loose end)
  - T1 revisit: `_is_unsafe_zip_member` + `_assert_no_symlinks_in_tree` already conform to sanitized-400 style — no T1 edits needed
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: FAIL (blocker: drain reader over-read past newline)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 285 passed (244 + 41 new)
  - Independent drain-reader repro: after cap+100 overflow line, subsequent `PREFIX:IMPORTANT\n` completely lost (returned `None`)
  - I001 import-sort lint detected in `kg1.py`
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/retrieval/loaders/kg1.py` drain reader blocker: `stream.read(_DISCARD_CHUNK)` over-reads past newline — when `discard` contains `AAA\nBBB...`, bytes `BBB...` belong to next line but are consumed; subsequent `_read_bounded_line` calls miss start of next line or entire line. Kind 2 independent repro confirmed byte-exact loss.
  - I001 import-sort lint in `kg1.py` (13 other pre-existing UP/F/C violations pre-date W2)
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/routers/retrieval.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/retrieval/loaders/kg1.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_validate_admin_dir.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_retrieval_config.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/retrieval/__init__.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/retrieval/test_kg1_drain.py`
  - `/Users/noelbao/Works/open-webui/.gitignore`

#### T2 retry

- **Master directive for retry**: PROCEED AS PLANNED. Surgical `readline(limit)` fix + 2 regression tests + I001 cleanup.
- **Principal work**:
  - `backend/open_webui/retrieval/loaders/kg1.py`: replaced `stream.read(_DISCARD_CHUNK)` with `stream.readline(_DISCARD_CHUNK)` — reads up to limit OR until first `\n` (inclusive); no bleed; bounded memory preserved; valid for both `io.BufferedReader` (Popen.stderr) and `io.BytesIO` (tests); I001 import-sort fixed
  - `backend/open_webui/test/retrieval/test_kg1_drain.py`: added `test_drain_preserves_following_line_after_truncation` and `test_drain_two_consecutive_oversized_lines` (15 → 17 tests)
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 1 retry total for T2)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 287 passed (285 + 2 regression tests)
  - 6 independent adversarial drain probes byte-exact: original bleed repro, chunk-boundary overflow (2×_DISCARD_CHUNK), empty line after oversize, triple oversize+short, EOF mid-oversize, exactly-at-cap boundary
  - Omitted-count arithmetic verified
  - I001 gone confirmed
  - 17/17 drain tests pass
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `stream.readline(limit)` is available on both `io.BufferedReader` and `io.BytesIO`, making it correct for both production (`Popen.stderr`) and test (`BytesIO`) usage without interface divergence
- **Files touched**:
  - `/Users/noelbao/Works/open-webui/backend/open_webui/retrieval/loaders/kg1.py`
  - `/Users/noelbao/Works/open-webui/backend/open_webui/test/retrieval/test_kg1_drain.py`

### T3

- **Cross-turn findings**: All 12 cross-validation grid checks PASS. Uniform HTTPException 400 shape, same reject-before-work idiom, same sanitization discipline across all three findings. Commit-separability confirmed: Unit A (F-3) ∩ Unit B (F-4+F-5+.gitignore) = ∅. W1 Playwright spec `admin-rag-settings.spec.ts` still TS-strict-clean. `bun run check` 9190 errors baseline preserved (W2 is backend-only).
- **Master directive for this turn**: PROCEED AS PLANNED for T3. Two-commit wave close (Unit A = F-3, Unit B = F-4+F-5+.gitignore). `_read_bounded_line` is internal — no acceptance grid adjustment needed.
- **Principal work**:
  - 12-step global-consistency grid executed; all PASS:
    1. End-to-end coherence: uniform HTTPException 400 shape, reject-before-work, sanitization discipline
    2. `_validate_admin_dir` — 1 def + 2 prod callers (`routers/retrieval.py:962`, `:1152`) + 6 test-only callers
    3. `_read_bounded_line` locality — def at `kg1.py:28`, 2 call sites (`kg1.py:422`, `:539`)
    4. W2 file set boundary: forbidden files (middleware.py, tools.py, sanitize.py, image_analysis.py, knowledge_export.py) — zero diff confirmed
    5. pytest: 287/9/5 — exact match to baseline
    6. `bun run check`: 9190 errors — baseline preserved
    7. `ruff format --check` on 9 files: exit 0
    8. Playwright `admin-rag-settings.spec.ts` TS-strict: exit 0
    9. 10 reveal-packet success criteria: all confirmed with file:line evidence
    10. Commit-separability: Unit A ∩ Unit B = ∅
    11. W1 retroactive: research grep zero, skills_upload 22/22
    12. Identity strings unambiguous: `soffice:{label}` vs `glm-ocr:{label}` in drain reader
- **Junior (kind 4) dispatches**: None
- **Evaluator verdict**: PASS (after 0 retries)
- **Evaluator commands run**:
  - `uv run pytest backend/open_webui/test/` — 287/9/5 baseline match confirmed
  - `bun run check` — 9190 errors (baseline preserved)
  - `uv run ruff format --check` on 9 W2-touched files — exit 0
  - `bunx tsc --noEmit --strict --target ES2020 --module ESNext --moduleResolution bundler e2e/tests/admin-rag-settings.spec.ts` — exit 0
  - 9 independent reproductions by kind 2 match; 5 integration-level adversarial checks PASS: error-message parity between F-4b/F-5 sites, no env state mutation between validators, identity strings unambiguous, T1 vs T2 sanitization consistent, commit-separability sanity confirmed
- **Junior (kind 5) dispatches**: None
- **Key findings**:
  - `backend/open_webui/routers/retrieval.py:842`: `_validate_admin_dir` reusable for future admin-path-setting validation — W3/W4 should call it rather than re-implement
  - All 10 reveal-packet success criteria met with file:line evidence

### T4 (if used)

None

### T5 (if used)

None

## What Was Tried But Did Not Work

- First drain reader implementation used `stream.read(_DISCARD_CHUNK)` + manual `find(b'\n')` for overflow drain. Failed: `read(N)` over-reads past the newline boundary, consuming bytes belonging to the next line. When `discard` contained `AAA\nBBB...`, bytes `BBB...` were silently discarded. Fixed in T2 retry via `stream.readline(limit)`, which reads up to the limit OR until the first `\n` (inclusive), whichever comes first.

## What Was Considered But Not Tried (Deferred)

- Generic `ERROR_MESSAGES.DEFAULT(str(e))` leakage pattern at `backend/open_webui/routers/skills.py:446-451` and across `tools.py` — pre-existing project-wide pattern that leaks `str(e)` in exception responses. Kind 3 directed not to pull into W2. Candidate for a future cross-cutting cleanup wave.
- 13 pre-existing ruff violations in `backend/open_webui/retrieval/loaders/kg1.py` (F401, UP035, UP045×5, UP006×2, UP015×2, C901) — pre-date W2, directed to leave in place.
- `RAG_RESEARCH_MODEL` orphan PersistentConfig (from W1) — remains deferred and out-of-scope for this run.

## What Was Given Up

None.

## Deferred Queue For Replanning

None.

## Unresolved Findings

None - wave closed clean.

## Files Modified (absolute paths)

**Unit A — F-3 (zip-slip):**
- `/Users/noelbao/Works/open-webui/backend/open_webui/routers/skills.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_skills_upload.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/skills/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/fixtures/skills/_build_fixtures.py`

**Unit B — F-4 + F-5 (drain reader + config validation):**
- `/Users/noelbao/Works/open-webui/backend/open_webui/routers/retrieval.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/retrieval/loaders/kg1.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_validate_admin_dir.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/routers/test_retrieval_config.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/retrieval/__init__.py`
- `/Users/noelbao/Works/open-webui/backend/open_webui/test/retrieval/test_kg1_drain.py`
- `/Users/noelbao/Works/open-webui/.gitignore`

Total: 3 source files modified + 4 new test modules + 3 new `__init__.py` + 1 fixture generator + `.gitignore`. 63 new tests. Zero files from forbidden list touched. Unit A ∩ Unit B = ∅.

## Behavioral Verifications Run

- `uv run pytest backend/open_webui/test/` — 287 passed / 9 pre-existing failed / 5 pre-existing collection errors (delta: +65 pass vs wave-start baseline of 222, 0 regressions)
- `uv run python -c "import open_webui"` — exit 0
- `uv run ruff format --check` on 9 W2-touched files — exit 0
- `uv run ruff check backend/open_webui/retrieval/loaders/kg1.py` — I001 removed; 13 pre-existing violations remain (pre-date W2)
- `bun run check` — 9190 errors (baseline preserved; W2 is backend-only, no frontend changes)
- `bunx tsc --noEmit --strict --target ES2020 --module ESNext --moduleResolution bundler e2e/tests/admin-rag-settings.spec.ts` — exit 0 (W1 Playwright spec still TS-strict-clean)
- T1 evaluator: 12 independent adversarial ZIP probes (nested traversal, dot-slash, mixed bad+good, deep symlinks, Windows drive, empty ZIP) — all rejected 400, no file leak post-rejection
- T2 retry evaluator: 6 independent adversarial drain probes (original bleed repro, chunk-boundary overflow at 2×_DISCARD_CHUNK, empty line after oversize, triple oversize+short, EOF mid-oversize, exactly-at-cap boundary) — all byte-exact correct; omitted-count arithmetic verified
- T3 evaluator: 9 independent reproductions match; 5 integration-level adversarial checks PASS (error-message parity F-4b/F-5, no env state mutation, identity strings unambiguous, T1 vs T2 sanitization consistent, commit-separability confirmed)
- Fixture generator (`_build_fixtures.py`) reproducibly produces 4 ZIPs; tests build in-memory so disk files are audit-only

## Wave Summary

W2 closed COMPLETE with 3 turns + 1 retry (T2 FAIL → retry PASS). All 3 findings fixed: F-3 (C-1) zip-slip/symlink/traversal hardening in skills upload via per-member `zf.extract()` pre-walk rejection with 22 tests; F-4 (C-4) bounded drain reader in `kg1.py` (64 KiB cap via `readline(limit)`, visibility marker, no OOM) plus `KG1_GLMOCR_PROJECT_DIR` validation via new `_validate_admin_dir` helper at `routers/retrieval.py:842`; F-5 (H-3) `RAG_KNOWLEDGE_EXPORT_DIR` validation via the same helper. 63 new tests total, all baselines preserved (287/9/5 pytest, 9190 check errors). The reusable `_validate_admin_dir` helper is available at `backend/open_webui/routers/retrieval.py:842` — W3/W4 should call it rather than re-implement for any future admin-path-setting validation. W2 sits as uncommitted working-tree changes against `HEAD=a84b62381` (W1 close); kind 7 will commit as 2 separable units (Unit A = F-3, Unit B = F-4+F-5+.gitignore) at wave close.

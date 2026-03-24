# 2026-03-24 22:55 Document Index Timeout Hardening

## Goal

- Investigate `Document index generation failed ... TimeoutError` for large PDF uploads and harden the index-generation path.

## Findings

- The timeout came from `backend/open_webui/routers/retrieval.py`, not from the organizer flow.
- The current live config uses `lmstudio.qwen3.5-9b` for document indexing (`AI_MEMORIES/environment.md`).
- Large OCR-derived PDFs were being sent to the index model in `128k` token chunks with `32k` overlap.
- Server logs showed exact `600s` failures on `Part 1/2`, which matches `RAG_DOCUMENT_INDEX_TIMEOUT`; this strongly indicates the index LLM call itself was timing out before completing the first very large chunk.
- `_call_index_llm()` did not cancel the in-flight future on timeout, so timed-out requests could continue consuming backend resources.

## Changes Made

- Reduced the initial document-index chunk plan to `48k` tokens with `8k` overlap.
- Added adaptive retry logic that recursively splits a timed-out chunk down to a `24k` minimum with overlap preserved.
- Reused a cached `tiktoken` encoder for token counting/splitting.
- Cancelled the in-flight future when `_call_index_llm()` times out.
- Preserved the previous small-document behavior by returning the single index body directly when no split was needed.

## Verification

- `./.venv/bin/python -m py_compile backend/open_webui/routers/retrieval.py`
- Behavioral extraction test confirmed adaptive retry splits an oversized chunk into smaller successful subchunks.
- Behavioral extraction test confirmed `_call_index_llm()` calls `future.cancel()` on timeout.

## Remaining Follow-up

- Re-run a real large PDF upload against the live server and confirm document indexing now completes without the exact 600s failure pattern.
- Keep separate attention on KG1/OCR timeouts against `127.0.0.1:11434`; they are related to extraction capacity, but distinct from this index-generation timeout.

## Expansion Log

- Expanded from the single user error line into log inspection, memory review, retrieval-path tracing, root-cause isolation, code hardening, and focused verification.
- Narrowed the issue from a generic "PDF indexing failure" to a specific "index LLM timeout on oversized 128k chunks under the live 9B model" failure mode.

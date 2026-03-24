# Direct Content Mode for New Chat Uploaded Files

**Created:** 2026-03-24
**Status:** Implemented and tested

## What

When a user opens a **new chat**, uploads files (type="file" only, no collections), and asks about those files, the RAG pipeline now **bypasses vector search entirely** and injects the full `.md` content + `.index.md` (AI-generated document index) directly as context.

## Why

- Vector search only returns top-k chunks, potentially losing important information
- For the first message, the user is clearly asking about the uploaded files
- The full `.md` + `.index.md` content gives the LLM complete document understanding
- Eliminates unnecessary embedding queries and round-trips

## How It Works

1. **Detection** (`middleware.py:chat_completion_files_handler`):
   - `only_uploaded_files`: all items have `type == "file"`
   - `is_new_chat`: `len(user_messages) <= 1`

2. **Content Loading** (`middleware.py:_build_direct_file_sources`):
   - Reads `file.data.content` (extracted .md) and `file.data.index_content` (.index.md)
   - Combines them with a `## Document Index` separator
   - Returns None (falls back to vector search) if any file lacks content

3. **Token Budget**: Still respects `RAG_FULL_DOCUMENT_MAX_TOKENS` — if total tokens exceed budget, sub-chat extraction kicks in

4. **Fallback**: If condition not met or content missing, falls through to normal vector-search path

## Files Modified

- `backend/open_webui/utils/middleware.py`:
  - Added `_build_direct_file_sources()` helper function
  - Added direct content mode block before existing vector search logic in `chat_completion_files_handler()`

## Key Implementation Details

- API sends files at **top level** of payload (`form_data["files"]`), NOT inside `metadata`
- The metadata construction at `main.py:1870` copies files into `metadata["files"]`
- Then `form_data["metadata"] = metadata` at `main.py:1925` makes them accessible via `body.get("metadata", {}).get("files")`

## Test Results

- Single file: 3,437 tokens injected, LLM correctly summarized the document
- Multi file: 4,591 tokens, LLM correctly compared two documents
- Multi-turn: Correctly fell through to normal vector search (14 sources, 863K tokens)

## Expansion Log

- 2026-03-24: Initial implementation and testing complete

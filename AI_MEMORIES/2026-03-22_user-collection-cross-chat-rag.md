# User Collection (Per-User Cross-Chat RAG) -- Implementation Log

## Date: 2026-03-22

**Status:** COMPLETED

## What Was Done

Implemented `RAG_USER_COLLECTION_ENABLED` feature: when enabled, every file uploaded by a user is automatically indexed into a `user-{user_id}` vector collection. This enables cross-chat RAG search -- Chat B can find documents uploaded in Chat A, with zero extra user action.

## How It Works

1. **On file upload/process:** In `process_file()`, after normal collection embedding, also embeds content chunks + .index.md chunks (if document index generation is enabled) into the `user-{user_id}` collection with `add=True` (append mode)
2. **On chat:** Middleware injects the user collection as a `collection_name` item into the files list for `get_sources_from_items`, enabling cross-chat retrieval even when no files are attached to the current chat
3. **On file delete:** Filters by `file_id` to remove only that file's entries from the user collection
4. **On re-process:** Deletes old entries by `file_id` first, then re-embeds fresh content
5. **Builtin tool:** `query_knowledge_files` also includes user collection in its search scope

## Key Implementation Details

- Collection name format: `user-{user_id}`
- Embedded with `add=True` (append mode, not replace)
- In `middleware.py`, `chat_completion_files_handler` now enters the processing block even with zero files attached (when user collection is enabled) -- this is what enables cross-chat RAG with no user action
- Dedup check prevents double-adding user collection if it's already present in the files list
- All existing pipeline features apply: qwen3 embedding prefix, full document context, sub-chat extraction

## Files Modified

| File                                                 | Change                                                                                      |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `backend/open_webui/config.py`                       | Added `RAG_USER_COLLECTION_ENABLED` PersistentConfig (default False)                        |
| `backend/open_webui/main.py`                         | Added app state initialization for `RAG_USER_COLLECTION_ENABLED`                            |
| `backend/open_webui/routers/retrieval.py`            | Embedding to user collection in `process_file()`, config form/endpoints, re-process cleanup |
| `backend/open_webui/routers/files.py`                | Cleanup on file deletion (filter by file_id)                                                |
| `backend/open_webui/tools/builtin.py`                | Added user collection to `query_knowledge_files` search scope                               |
| `backend/open_webui/utils/middleware.py`             | Injected user collection into `chat_completion_files_handler` files list                    |
| `src/lib/components/admin/Settings/Documents.svelte` | Added UI toggle for User Collection                                                         |

## Architecture Pattern

```
User uploads file -> process_file()
                        |
          +-------------+-------------+
          |                           |
    Normal collection         User collection
    (per knowledge/chat)      (user-{user_id})
          |                           |
     chunks + index            chunks + index
          |                           |
          +-----> ChromaDB <----------+
                     |
         +-----------+-----------+
         |                       |
  Per-chat retrieval     Cross-chat retrieval
  (normal flow)          (user collection injected
                          by middleware even when
                          no files attached)
```

## Status: COMPLETED

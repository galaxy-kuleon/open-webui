# Enhanced RAG Pipeline + Knowledge System — Full Session Log

## Date: 2026-03-22

**Status:** COMPLETED

## What was done

### Phase A: Full Document Context (RAG_FULL_DOCUMENT_CONTEXT)

- After chunk retrieval finds relevant chunks, traces back to source files via file_id
- Loads full .md content from Files table, deduplicates by file_id
- Replaces chunks with complete source documents in context
- New function: `expand_sources_to_full_documents()` in middleware.py

### Phase B: Sub-Chat Map-Reduce Extraction

- When total source document tokens exceed RAG_FULL_DOCUMENT_MAX_TOKENS (default 128000)
- Spawns sub-completions (same model as chat) to extract query-relevant info per document
- Concurrency controlled by RAG_SUBCHAT_CONCURRENCY (default 3) via asyncio.Semaphore
- Graceful fallback: if extraction fails, uses original full content

### Phase C: Qwen3 Embedding Query Prefix

- Upgraded RAG_EMBEDDING_QUERY_PREFIX, CONTENT_PREFIX, PREFIX_FIELD_NAME from env-only to PersistentConfig
- Added UI fields in Admin > Documents > Embedding section
- Auto-fills prefix when selecting Ollama + qwen3-embedding models

## Qwen3 Embedding Model Info

| Model                   | Parameters | Embedding Dimension | Context Length |
| ----------------------- | ---------- | ------------------- | -------------- |
| qwen3-embedding:4b-fp16 | 4.02B      | 2560                | 40960 tokens   |
| qwen3-embedding:8b-fp16 | 7.57B      | 4096                | 40960 tokens   |

### Query Prefix Format (instruction-sensitive)

```
Instruct: Given a web search query, retrieve relevant passages that answer the query.
Query: {actual_query}
```

- Content/Document prefix: leave empty
- PREFIX_FIELD_NAME: leave empty (Ollama prepends to text, no native instruction field)

### Vector DB Dimension Constraints

| Vector DB        | Default Dim | Notes for qwen3-embedding                                                           |
| ---------------- | ----------- | ----------------------------------------------------------------------------------- |
| Chroma (default) | Auto-detect | No issue                                                                            |
| Milvus           | Auto-detect | No issue                                                                            |
| Qdrant           | Auto-detect | No issue                                                                            |
| Pinecone         | 1536        | Set PINECONE_DIMENSION=2560 or 4096                                                 |
| pgvector         | 1536        | Set PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH=4096 + PGVECTOR_USE_HALFVEC=true (for 8b) |
| MariaDB          | 1536        | Set MARIADB_VECTOR_INITIALIZE_MAX_VECTOR_LENGTH=4096                                |
| OpenGauss        | 1536        | Set OPENGAUSS_INITIALIZE_MAX_VECTOR_LENGTH=4096                                     |
| Oracle           | 768         | Set ORACLE_VECTOR_LENGTH=4096                                                       |

## Files Modified

- `backend/open_webui/config.py` - RAG_FULL_DOCUMENT_CONTEXT, RAG_FULL_DOCUMENT_MAX_TOKENS, RAG_SUBCHAT_CONCURRENCY, DEFAULT_RAG_SUBCHAT_EXTRACTION_TEMPLATE + PersistentConfig prefix upgrade
- `backend/open_webui/main.py` - imports + app state init
- `backend/open_webui/routers/retrieval.py` - ConfigForm + GET/POST endpoints
- `backend/open_webui/utils/middleware.py` - expand_sources_to_full_documents(), estimate_tokens(), extract_relevant_content_from_document(), Phase A+B orchestration in chat_completion_files_handler()
- `backend/open_webui/retrieval/utils.py` - .value access for PersistentConfig prefix objects (Wave 3 agent)
- `src/lib/components/admin/Settings/Documents.svelte` - Full Document Context switch, Max Tokens, Sub-Chat Concurrency, Embedding prefix fields

## Key Learnings

- Open WebUI's generate_queries() uses LLM to create optimized search queries BEFORE embedding - the query prefix applies to these generated queries
- `generate_chat_completion()` with bypass_filter=True is the pattern for internal sub-completions
- Sub-chat payload without metadata.files prevents recursive RAG triggering
- Files.get_file_by_id(file_id).data.get("content") gives full processed text
- threading.Semaphore for sync code, asyncio.Semaphore for async code

### Phase D: Document Index Generation (RAG_DOCUMENT_INDEX_GENERATION)

- AI generates structured index per document: entities, events, temporal info, relationships, key facts
- Index content stored as separate ChromaDB entry with `metadata.type="index"`
- Uses configurable model via `RAG_DOCUMENT_INDEX_MODEL` (e.g., `lmstudio.qwen3.5-9b`)
- Both `.md` content and `index_content` injected into context when Full Document Context is enabled

### Phase E: Knowledge Export System

- NEW file: `backend/open_webui/utils/knowledge_export.py`
- Exports `.md` + `.index.md` to filesystem (`RAG_KNOWLEDGE_EXPORT_DIR`) with YAML frontmatter
- Background job queue: `threading.Queue` + daemon worker thread
- OpenCode subprocess (`opencode run --dir <kb_dir> --model <model>`) organizes files into semantic multi-level directories
- OpenCode skill at `knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md`
- OpenCode config at `knowledge-base/opencode.json` with `"permission": "allow"`
- Tested: files auto-sorted to `legal-corporate/governance/`, `personal/finance/banking/`, `personal/travel/flights/`, etc.

### Phase F: /research Chat Command

- NEW file: `backend/open_webui/utils/research.py`
- Chat command `/research <query>` spawns OpenCode to research knowledge base directory
- Report saved to `kb_dir/_reports/` and injected into chat context as a source
- Requires RAG_KNOWLEDGE_EXPORT_ENABLED + RAG_KNOWLEDGE_EXPORT_DIR

### Phase G: Processing Progress UX

- Added granular status updates: `processing:extracting`, `processing:embedding`, `processing:indexing`
- SSE streaming sends step-specific status to frontend
- Frontend FileItem shows spinner + status text during processing
- Added `onProgress` callback to `uploadFile()` API function
- FileItem.svelte: new `statusText` prop

## Additional Files Modified/Created (Phases D-G)

- NEW: `backend/open_webui/utils/knowledge_export.py`
- NEW: `backend/open_webui/utils/research.py`
- `src/lib/components/chat/MessageInput.svelte` -- /research command handling
- `src/lib/components/chat/Chat.svelte` -- /research integration
- `src/lib/components/common/FileItem.svelte` -- Progress text display
- `src/lib/apis/files/index.ts` -- onProgress callback
- `knowledge-base/opencode.json` -- OpenCode config
- `knowledge-base/.opencode/skills/knowledge-organizer/SKILL.md` -- Organization skill

## Status: COMPLETED

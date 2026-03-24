# Unexplored Directions

**Last updated:** 2026-03-22

## RAG & Document Processing

### U-1: Hybrid Retrieval (Dense + Sparse)

- **Idea:** Combine vector similarity (Qwen3 embeddings) with BM25/sparse retrieval for better recall
- **Why consider:** Dense retrieval alone can miss keyword-exact matches; hybrid often outperforms either alone
- **Effort:** Medium -- Open WebUI already has embedding infrastructure

### U-2: Cross-Document Relationship Graphs

- **Idea:** Build entity/relationship graphs across documents, not just per-document indexes
- **Why consider:** Current index generation is per-document; cross-document connections would enable deeper reasoning
- **Effort:** High -- needs graph storage, entity resolution, relationship extraction pipeline

### U-3: Incremental Re-indexing

- **Idea:** When documents are updated, only re-process changed sections instead of full re-extraction
- **Why consider:** Current pipeline re-processes entire documents on update
- **Effort:** Medium -- needs content diff detection

### U-4: DeepSeek-OCR-2 via MLX

- **Idea:** When DeepSeek-OCR-2 becomes available on Ollama, switch from glm-ocr to it for potentially better OCR quality
- **Why consider:** DeepSeek-OCR-2 benchmarks higher on multi-column and math; MLX path is fast on Apple Silicon
- **Status:** Blocked -- DeepSeek-OCR-2 not yet on Ollama as of 2026-03-22

### U-5: Table-Aware Chunking

- **Idea:** Split documents at semantic boundaries (sections, tables) rather than fixed token counts
- **Why consider:** Current token-based splitting can cut tables and sections in half
- **Effort:** Medium -- needs content-aware parser

## Agent & Skills

### U-6: Agent Skill Marketplace / Sharing

- **Idea:** Allow users to share agent skills as downloadable packages
- **Why consider:** Currently skills are manually uploaded as zips
- **Effort:** High -- needs packaging format, discovery, versioning

### U-7: Multi-Agent Workflows

- **Idea:** Chain multiple agent skills together in a pipeline
- **Why consider:** Complex tasks could benefit from specialized agents cooperating
- **Effort:** High -- needs orchestration layer

## Knowledge Base

### U-8: Auto-Tagging / Classification

- **Idea:** Automatically tag documents with categories, topics, entities upon upload
- **Why consider:** Currently relies on OpenCode organizer which is batch-oriented
- **Effort:** Medium -- could use existing index generation infrastructure

### U-9: Knowledge Base Search UI

- **Idea:** Dedicated UI for browsing/searching the organized knowledge base filesystem
- **Why consider:** Currently the organized knowledge base is only accessible via filesystem or /research command
- **Effort:** Medium -- needs new frontend component + API endpoint

### U-10: Versioned Knowledge Snapshots

- **Idea:** Git-track the knowledge base directory for version history
- **Why consider:** Documents change, organizations evolve; version control enables rollback and audit
- **Effort:** Low -- just `git init` in the knowledge base dir

### U-11: User Collection Management UI

- **Idea:** UI for users to browse/manage their user collection -- see what's indexed, remove specific files, view collection stats
- **Why consider:** With RAG_USER_COLLECTION_ENABLED, users accumulate documents across chats but have no visibility into what's in their collection
- **Effort:** Medium -- needs new API endpoints + frontend component
- **Unlocked by:** EG-5 (User Collection feature, 2026-03-22)

### U-12: User Collection Selective Opt-In (Per-File)

- **Idea:** Per-file or per-upload toggle for "include in my personal collection"
- **Why consider:** Some files are temporary/one-off and shouldn't pollute the user's cross-chat search space
- **Effort:** Low-Medium -- add a flag to upload UI + check in process_file()

### U-13: User Collection Per-Chat Opt-Out

- **Idea:** Per-chat toggle to disable cross-chat RAG for specific conversations
- **Why consider:** Currently user collection search is always-on when globally enabled. Some chats may be for unrelated topics where cross-document noise hurts more than helps.
- **Effort:** Low -- add a chat-level setting that middleware checks before injecting user collection
- **Unlocked by:** EG-5 (User Collection feature, 2026-03-22)

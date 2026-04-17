import { test, expect } from "@playwright/test";
import {
  loginAsAdmin,
  getRAGConfigViaAPI,
  updateRAGConfigViaAPI,
  getEmbeddingConfigViaAPI,
  updateEmbeddingConfigViaAPI,
} from "../helpers/admin";

/**
 * Admin RAG Settings — round-trip smoke test covering 10 controls.
 *
 * Endpoints exercised:
 *   GET/POST /api/v1/retrieval/config         — 7 fields (RAG config)
 *   GET/POST /api/v1/retrieval/embedding      — 3 fields (embedding config)
 *
 * Pattern:
 *   beforeEach: login as admin, snapshot both endpoint responses
 *   test:       mutate all 10 fields to non-default values → re-read → assert survival
 *   afterEach:  restore both endpoints from the captured snapshot (NOT hardcoded defaults)
 *
 * The afterEach restoration is always-on (Playwright's afterEach runs after both
 * passing and failing tests), so the environment is always left clean for reruns.
 *
 * Mutation values are chosen to be distinctive — unlikely to be the current defaults —
 * so a false-positive pass (where the server ignores the write and returns the original)
 * is detectable.
 */

test.describe("Admin RAG Settings — 10-control round-trip", () => {
  /** Captured at beforeEach; restored at afterEach — never hardcoded */
  let originalRAG: Record<string, unknown>;
  let originalEmbedding: Record<string, unknown>;

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    originalRAG = await getRAGConfigViaAPI(page);
    originalEmbedding = await getEmbeddingConfigViaAPI(page);
  });

  test.afterEach(async ({ page }) => {
    // Restore all 7 RAG fields from the captured snapshot.
    // Login again: afterEach runs in a fresh page context after some test runners
    // clear page state, but Playwright reuses the same page between beforeEach/test/afterEach
    // within a single test — the token is still in localStorage.
    await updateRAGConfigViaAPI(page, {
      RAG_FULL_DOCUMENT_CONTEXT: originalRAG.RAG_FULL_DOCUMENT_CONTEXT,
      RAG_FULL_DOCUMENT_MAX_TOKENS: originalRAG.RAG_FULL_DOCUMENT_MAX_TOKENS,
      RAG_SUBCHAT_CONCURRENCY: originalRAG.RAG_SUBCHAT_CONCURRENCY,
      RAG_USER_COLLECTION_ENABLED: originalRAG.RAG_USER_COLLECTION_ENABLED,
      RAG_DOCUMENT_INDEX_GENERATION: originalRAG.RAG_DOCUMENT_INDEX_GENERATION,
      RAG_DOCUMENT_INDEX_MODEL: originalRAG.RAG_DOCUMENT_INDEX_MODEL,
      RAG_DOCUMENT_INDEX_TIMEOUT: originalRAG.RAG_DOCUMENT_INDEX_TIMEOUT,
    });

    // Restore all 3 embedding prefix fields from the captured snapshot.
    await updateEmbeddingConfigViaAPI(page, {
      RAG_EMBEDDING_QUERY_PREFIX: originalEmbedding.RAG_EMBEDDING_QUERY_PREFIX,
      RAG_EMBEDDING_CONTENT_PREFIX: originalEmbedding.RAG_EMBEDDING_CONTENT_PREFIX,
      RAG_EMBEDDING_PREFIX_FIELD_NAME: originalEmbedding.RAG_EMBEDDING_PREFIX_FIELD_NAME,
    });
  });

  // ---------------------------------------------------------------------------
  // Round-trip: all 10 controls mutated → re-read → each asserted to have survived
  //
  // Mutation strategy:
  //   Booleans: toggled with !original (detects stuck-at-default failures)
  //   Numbers:  distinctive values far from typical defaults
  //   Strings:  "e2e-" prefixed, recognisable in logs
  //
  // Assertion strategy:
  //   After writing, we perform a fresh GET and compare each field individually.
  //   This verifies the server persisted the write, not just that the POST returned 200.
  // ---------------------------------------------------------------------------
  test("all 10 RAG + embedding config controls survive a write-then-read round-trip", async ({
    page,
  }) => {
    // ------------------------------------------------------------------
    // Step 1: compute mutation values — toggles computed against original
    // ------------------------------------------------------------------
    const mutatedRAG = {
      RAG_FULL_DOCUMENT_CONTEXT: !originalRAG.RAG_FULL_DOCUMENT_CONTEXT,
      RAG_FULL_DOCUMENT_MAX_TOKENS: 12345,
      RAG_SUBCHAT_CONCURRENCY: 7,
      RAG_USER_COLLECTION_ENABLED: !originalRAG.RAG_USER_COLLECTION_ENABLED,
      RAG_DOCUMENT_INDEX_GENERATION: !originalRAG.RAG_DOCUMENT_INDEX_GENERATION,
      RAG_DOCUMENT_INDEX_MODEL: "test-model-e2e",
      RAG_DOCUMENT_INDEX_TIMEOUT: 600,
    };

    const mutatedEmbedding = {
      RAG_EMBEDDING_QUERY_PREFIX: "query-e2e: ",
      RAG_EMBEDDING_CONTENT_PREFIX: "content-e2e: ",
      RAG_EMBEDDING_PREFIX_FIELD_NAME: "e2e-field",
    };

    // ------------------------------------------------------------------
    // Step 2: write mutations to both endpoints
    // ------------------------------------------------------------------
    await updateRAGConfigViaAPI(page, mutatedRAG);
    await updateEmbeddingConfigViaAPI(page, mutatedEmbedding);

    // ------------------------------------------------------------------
    // Step 3: re-read both endpoints (fresh GET — not the cached POST response)
    // ------------------------------------------------------------------
    const afterRAG = await getRAGConfigViaAPI(page);
    const afterEmbedding = await getEmbeddingConfigViaAPI(page);

    // ------------------------------------------------------------------
    // Step 4: assert each of the 10 controls survived
    //
    // RAG config — 7 fields
    // ------------------------------------------------------------------
    expect(afterRAG.RAG_FULL_DOCUMENT_CONTEXT).toBe(mutatedRAG.RAG_FULL_DOCUMENT_CONTEXT);
    expect(afterRAG.RAG_FULL_DOCUMENT_MAX_TOKENS).toBe(mutatedRAG.RAG_FULL_DOCUMENT_MAX_TOKENS);
    expect(afterRAG.RAG_SUBCHAT_CONCURRENCY).toBe(mutatedRAG.RAG_SUBCHAT_CONCURRENCY);
    expect(afterRAG.RAG_USER_COLLECTION_ENABLED).toBe(mutatedRAG.RAG_USER_COLLECTION_ENABLED);
    expect(afterRAG.RAG_DOCUMENT_INDEX_GENERATION).toBe(mutatedRAG.RAG_DOCUMENT_INDEX_GENERATION);
    expect(afterRAG.RAG_DOCUMENT_INDEX_MODEL).toBe(mutatedRAG.RAG_DOCUMENT_INDEX_MODEL);
    expect(afterRAG.RAG_DOCUMENT_INDEX_TIMEOUT).toBe(mutatedRAG.RAG_DOCUMENT_INDEX_TIMEOUT);

    // Embedding config — 3 fields
    expect(afterEmbedding.RAG_EMBEDDING_QUERY_PREFIX).toBe(
      mutatedEmbedding.RAG_EMBEDDING_QUERY_PREFIX,
    );
    expect(afterEmbedding.RAG_EMBEDDING_CONTENT_PREFIX).toBe(
      mutatedEmbedding.RAG_EMBEDDING_CONTENT_PREFIX,
    );
    expect(afterEmbedding.RAG_EMBEDDING_PREFIX_FIELD_NAME).toBe(
      mutatedEmbedding.RAG_EMBEDDING_PREFIX_FIELD_NAME,
    );
  });
});

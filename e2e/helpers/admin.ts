import { type Page } from '@playwright/test';
import { login } from './auth';

/**
 * Admin helpers for Open WebUI E2E tests.
 *
 * Design: pure functions over plain data — no classes, no shared mutable state.
 * All API helpers use page.request (Playwright's HTTP client) which shares the
 * same auth cookies as the page context, augmented with a Bearer token read
 * from localStorage.
 *
 * Environment variables:
 *   ADMIN_EMAIL    — admin account email      (default: admin@localhost)
 *   ADMIN_PASSWORD — admin account password   (default: admin)
 */

/** Immutable admin credentials — read from process.env with documented fallbacks */
export const ADMIN_CREDENTIALS = {
	email: process.env.ADMIN_EMAIL ?? 'admin@localhost',
	password: process.env.ADMIN_PASSWORD ?? 'admin'
} as const;

/**
 * Log in to Open WebUI as the admin user.
 *
 * Thin wrapper around the shared `login()` helper — does not reimplement the login flow.
 *
 * @param page - Playwright Page instance
 */
export async function loginAsAdmin(page: Page): Promise<void> {
	await login(page, ADMIN_CREDENTIALS.email, ADMIN_CREDENTIALS.password);
}

/**
 * Retrieve the current RAG/retrieval configuration via the backend API.
 *
 * Mechanism: reads the Bearer token from localStorage (set by the Open WebUI frontend
 * upon successful login), then issues a GET to /api/v1/retrieval/config.
 *
 * @param page - Playwright Page instance (must be logged in)
 * @returns The full RAG config object from the server
 */
export async function getRAGConfigViaAPI(page: Page): Promise<Record<string, unknown>> {
	const token = await page.evaluate(() => localStorage.getItem('token'));
	const response = await page.request.get('/api/v1/retrieval/config', {
		headers: { Authorization: `Bearer ${token}` }
	});
	return response.json();
}

/**
 * Update the RAG/retrieval configuration via the backend API.
 *
 * The backend ConfigForm uses Optional fields with ternary handlers — partial payloads
 * are fully supported. You only need to include fields you want to change.
 * Fields absent from the payload retain their current server-side values.
 *
 * Pattern for surgical toggle:
 *   await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: false });
 *
 * Pattern for safe restore (to ensure unchanged fields survive):
 *   const original = await getRAGConfigViaAPI(page);
 *   // ... test ...
 *   await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: original.IMAGE_ANALYSIS_ENABLED });
 *
 * @param page    - Playwright Page instance (must be logged in as admin)
 * @param payload - Partial config payload. Only provided fields are updated.
 */
export async function updateRAGConfigViaAPI(
	page: Page,
	payload: Record<string, unknown>
): Promise<void> {
	const token = await page.evaluate(() => localStorage.getItem('token'));
	const response = await page.request.post('/api/v1/retrieval/config/update', {
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		data: payload
	});
	if (!response.ok()) {
		const body = await response.text();
		throw new Error(`updateRAGConfigViaAPI failed: ${response.status()} — ${body.slice(0, 200)}`);
	}
}

/**
 * Retrieve the current embedding configuration via the backend API.
 *
 * Mechanism: reads the Bearer token from localStorage (set by the Open WebUI frontend
 * upon successful login), then issues a GET to /api/v1/retrieval/embedding.
 *
 * Note: the frontend TypeScript type `EmbeddingModelUpdateForm` (retrieval/index.ts:194-200)
 * does NOT declare the three prefix fields (RAG_EMBEDDING_QUERY_PREFIX,
 * RAG_EMBEDDING_CONTENT_PREFIX, RAG_EMBEDDING_PREFIX_FIELD_NAME). The backend
 * accepts and returns them — this is a pre-existing stale type issue in src/, not a helper bug.
 *
 * @param page - Playwright Page instance (must be logged in)
 * @returns The full embedding config object from the server
 */
export async function getEmbeddingConfigViaAPI(page: Page): Promise<Record<string, unknown>> {
	const token = await page.evaluate(() => localStorage.getItem('token'));
	const response = await page.request.get('/api/v1/retrieval/embedding', {
		headers: { Authorization: `Bearer ${token}` }
	});
	return response.json();
}

/**
 * Update the embedding configuration via the backend API.
 *
 * Accepts `Record<string, unknown>` rather than the frontend's stale `EmbeddingModelUpdateForm`
 * type (which omits the three prefix fields). The backend accepts partial payloads including
 * the prefix fields — only provided fields are updated.
 *
 * Pattern for safe restore (to ensure unchanged fields survive):
 *   const original = await getEmbeddingConfigViaAPI(page);
 *   // ... test ...
 *   await updateEmbeddingConfigViaAPI(page, {
 *     RAG_EMBEDDING_QUERY_PREFIX: original.RAG_EMBEDDING_QUERY_PREFIX,
 *     RAG_EMBEDDING_CONTENT_PREFIX: original.RAG_EMBEDDING_CONTENT_PREFIX,
 *     RAG_EMBEDDING_PREFIX_FIELD_NAME: original.RAG_EMBEDDING_PREFIX_FIELD_NAME,
 *   });
 *
 * @param page    - Playwright Page instance (must be logged in as admin)
 * @param payload - Partial embedding config payload. Only provided fields are updated.
 */
export async function updateEmbeddingConfigViaAPI(
	page: Page,
	payload: Record<string, unknown>
): Promise<void> {
	const token = await page.evaluate(() => localStorage.getItem('token'));
	const response = await page.request.post('/api/v1/retrieval/embedding/update', {
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		data: payload
	});
	if (!response.ok()) {
		const body = await response.text();
		throw new Error(
			`updateEmbeddingConfigViaAPI failed: ${response.status()} — ${body.slice(0, 200)}`
		);
	}
}

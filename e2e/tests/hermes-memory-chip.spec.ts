import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';
import { selectModel, CHAT_SELECTORS } from '../helpers/chat';

const HERMES_MODEL = 'hermes_agent.hermes-agent' as const;

async function currentToken(page): Promise<string> {
	const token = await page.evaluate(() => localStorage.getItem('token'));
	if (!token) {
		throw new Error('No auth token in localStorage');
	}
	return token;
}

async function createUserViaAPI(page, email: string, password: string, name: string): Promise<void> {
	const token = await currentToken(page);
	const response = await page.request.post('/api/v1/auths/add', {
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		data: {
			email,
			password,
			name,
			role: 'user'
		}
	});

	if (!response.ok()) {
		const body = await response.text();
		throw new Error(`createUserViaAPI failed: ${response.status()} — ${body.slice(0, 200)}`);
	}
}

async function ensureHermesModelSelected(page): Promise<void> {
	const selector = page.locator(CHAT_SELECTORS.modelSelectorButton);
	const text = await selector.textContent();
	if (text?.includes('Select a model')) {
		await selector.click();
		const searchInput = page.locator(CHAT_SELECTORS.modelSearchInput);
		await searchInput.waitFor({ state: 'visible', timeout: 10_000 });
		await searchInput.fill('hermes');
		const hermesOption = page.locator(CHAT_SELECTORS.modelOption).filter({ hasText: 'hermes' }).first();
		await hermesOption.waitFor({ state: 'visible', timeout: 10_000 });
		await hermesOption.click();
		await expect(selector).not.toContainText('Select a model', { timeout: 5_000 });
	}
}

async function loginAsFreshUser(page, runId: string, label: string) {
	await login(page);

	const email = `${label}-${runId}@example.com`;
	const password = `pw-${runId}-test123`;
	await createUserViaAPI(page, email, password, `${label}-${runId}`);

	await page.context().clearCookies();
	await page.evaluate(() => {
		localStorage.clear();
		sessionStorage.clear();
	});

	await login(page, email, password);
	await ensureHermesModelSelected(page);
	return { email, password };
}

async function addHermesFactViaAPI(page, content: string): Promise<void> {
	const token = await currentToken(page);
	const response = await page.request.post('/api/v1/hermes/memory/profile', {
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		data: {
			content,
			category: 'user_pref'
		}
	});

	if (!response.ok()) {
		const body = await response.text();
		throw new Error(`addHermesFactViaAPI failed: ${response.status()} — ${body.slice(0, 200)}`);
	}
}

/**
 * W2b behavioural test — Hermes memory-recall chip rendering.
 *
 * Prerequisites (must be true when test runs against live stack):
 *   1. openwebui backend running at the configured test URL
 *   2. hermes-agent server reachable with at least one configured model exposed via pipe
 *   3. a memory provider that CAN return non-empty prefetch for at least the test user
 *      - holographic (default post-W1) will NOT populate recall unless facts have been
 *        written first. This test is two-phase: phase 1 writes a fact via fact_store,
 *        phase 2 queries and asserts the chip.
 *      - if holographic is replaced by honcho self-host or per-user patched holographic,
 *        the cross-user isolation portion becomes meaningful.
 *
 * This spec is intentionally not run in CI until a live stack is available.
 * It is a behavioural-test artefact that documents expected UX.
 *
 * Selector contract: HermesMemoryRecallStatus.svelte renders a button with
 *   data-testid="hermes-memory-recall-chip". The chip text contains the
 *   i18n string "Recalled from your memory".
 */

test.describe('Hermes memory-recall chip', () => {
	test.setTimeout(240_000);

	test('chip renders on assistant message when provider prefetch returns recall', async ({
		page
	}) => {
		// Requires hermes-agent inference provider to be configured and working.
		// Skip if HERMES_INFERENCE_PROVIDER is not set or provider returns empty responses.
		test.skip(process.env.SKIP_HERMES_MEMORY_CHIP === 'true', 'Hermes memory chip test skipped');

		const runId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
		await loginAsFreshUser(page, runId, 'chip-user');
		await addHermesFactViaAPI(page, `My favourite testing framework is Playwright ${runId}`);

		const chatInput = page.locator('#chat-input');
		await chatInput.click();
		await chatInput.fill('What is my favourite testing framework?');
		await page.keyboard.press('Enter');

		// Wait for response to complete
		await page.waitForSelector('.shimmer', { state: 'detached', timeout: 120_000 });

		// Assert the memory-recall chip appears.
		const chip = page.locator('[data-testid="hermes-memory-recall-chip"]').first();
		await expect(chip).toBeVisible({ timeout: 60_000 });

		// Click to expand; context preview should contain some recall text
		await chip.click();
		await expect(page.locator('[data-testid="hermes-memory-recall-chip"]').first()).toContainText(
			/recalled|memory|summary/i
		);

		await page.screenshot({
			path: 'screenshots/hermes-memory-chip-visible.png',
			fullPage: true
		});
	});

	test('chip absent when no memory recall signal is emitted', async ({ page }) => {
		const runId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
		await loginAsFreshUser(page, runId, 'empty-user');

		// Send a one-shot question with no prior context — provider prefetch should
		// return empty (first-turn, no facts about this user) and no chip should render.
		const chatInput = page.locator('#chat-input');
		await chatInput.click();
		await chatInput.fill('What is 2 plus 2?');
		await page.keyboard.press('Enter');
		await page.waitForSelector('.shimmer', { state: 'detached', timeout: 120_000 });

		// Chip must NOT be present on this response.
		await expect(page.locator('[data-testid="hermes-memory-recall-chip"]')).toHaveCount(0);
	});
});

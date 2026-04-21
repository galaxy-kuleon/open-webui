import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';

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
		await login(page);

		// Phase 1 — seed a fact the provider should later recall.
		// This leverages the hermes fact_store tool exposed when holographic is active,
		// OR honcho's conclusion-writing when honcho is active. Either way, a
		// proactive user message establishes a memory the agent will see next turn.
		await page.locator('textarea').first().fill(
			'Please remember this: my favourite testing framework is Playwright.'
		);
		await page.keyboard.press('Enter');
		await page.waitForSelector('.shimmer', { state: 'detached', timeout: 120_000 });

		// Phase 2 — new turn that should trigger recall.
		await page.locator('textarea').first().fill('What is my favourite testing framework?');
		await page.keyboard.press('Enter');

		// Assert the memory-recall chip appears.
		const chip = page.locator('[data-testid="hermes-memory-recall-chip"]').first();
		await expect(chip).toBeVisible({ timeout: 60_000 });

		// Click to expand; context preview should contain Playwright
		await chip.click();
		await expect(page.locator('[data-testid="hermes-memory-recall-chip"]').first()).toContainText(
			/playwright|testing framework/i
		);

		await page.screenshot({
			path: 'screenshots/hermes-memory-chip-visible.png',
			fullPage: true
		});
	});

	test('chip absent when no memory recall signal is emitted', async ({ page }) => {
		await login(page);

		// Send a one-shot question with no prior context — provider prefetch should
		// return empty (first-turn, no facts about this user) and no chip should render.
		await page.locator('textarea').first().fill('What is 2 plus 2?');
		await page.keyboard.press('Enter');
		await page.waitForSelector('.shimmer', { state: 'detached', timeout: 120_000 });

		// Chip must NOT be present on this response.
		await expect(page.locator('[data-testid="hermes-memory-recall-chip"]')).toHaveCount(0);
	});
});

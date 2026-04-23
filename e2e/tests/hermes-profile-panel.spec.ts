import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';

/**
 * W3b behavioural test — Hermes memory profile panel UI.
 *
 * Prerequisites:
 *   1. openwebui backend running with /api/v1/hermes/memory/* router registered (W3)
 *   2. hermes-agent reachable with /v1/memory/tool endpoint live (W3)
 *   3. active provider that supports fact_store (holographic OK for W3b)
 *
 * Selector contract (from SettingsModal.svelte + HermesMemory.svelte):
 *   - settings dialog: getByRole('dialog')
 *   - hermes tab:      button[role="tab"] with text "Hermes Memory"
 *   - panel:           [data-testid="hermes-memory-panel"]
 *   - new-fact:        [data-testid="hermes-memory-new-fact-input"]
 *   - add button:      [data-testid="hermes-memory-add-button"]
 *   - search input:    [data-testid="hermes-memory-search-input"]
 *   - fact list:       [data-testid="hermes-memory-fact-list"]
 *   - delete btn:      [data-testid="hermes-memory-delete-button"]
 *
 * This spec is intentionally not auto-run until a live stack is available.
 */

test.describe('Hermes memory profile panel', () => {
	test.setTimeout(120_000);

	test('panel is reachable via Settings → Hermes memory tab', async ({ page }) => {
		await login(page);

		// Open Settings modal (keyboard shortcut or menu click — follow existing
		// patterns from other settings e2e tests in this repo)
		await page.keyboard.press('Control+.');
		await expect(page.getByRole('dialog').first()).toBeVisible({ timeout: 15_000 });

		// Click the Hermes memory tab
		await page.getByRole('tab', { name: 'Hermes Memory' }).click();

		// Panel visible
		await expect(page.locator('[data-testid="hermes-memory-panel"]')).toBeVisible();
	});

	test('add a fact and it appears in the list', async ({ page }) => {
		await login(page);
		await page.keyboard.press('Control+.');
		await expect(page.getByRole('dialog').first()).toBeVisible({ timeout: 15_000 });
		await page.getByRole('tab', { name: 'Hermes Memory' }).click();
		await expect(page.locator('[data-testid="hermes-memory-panel"]')).toBeVisible();

		const unique = `test-fact-${Date.now()}`;
		await page
			.locator('[data-testid="hermes-memory-new-fact-input"]')
			.fill(`I prefer Playwright for testing — ${unique}`);
		await page.locator('[data-testid="hermes-memory-add-button"]').click();

		// Fact should now appear in the list (after loadFacts re-fetches)
		await expect(page.locator('[data-testid="hermes-memory-fact-list"]')).toContainText(unique, {
			timeout: 15_000
		});
	});

	test('search returns relevant facts', async ({ page }) => {
		await login(page);
		await page.keyboard.press('Control+.');
		await expect(page.getByRole('dialog').first()).toBeVisible({ timeout: 15_000 });
		await page.getByRole('tab', { name: 'Hermes Memory' }).click();

		// Seed a fact to search for
		const token = `searchable-${Date.now()}`;
		await page
			.locator('[data-testid="hermes-memory-new-fact-input"]')
			.fill(`${token} I love TypeScript`);
		await page.locator('[data-testid="hermes-memory-add-button"]').click();
		await page.waitForTimeout(1500); // let backend settle

		// Search
		await page.locator('[data-testid="hermes-memory-search-input"]').fill(token);
		await page.locator('[data-testid="hermes-memory-search-input"]').press('Enter');

		// Result appears somewhere in the panel
		await expect(page.locator('[data-testid="hermes-memory-panel"]')).toContainText(token, {
			timeout: 10_000
		});
	});
});

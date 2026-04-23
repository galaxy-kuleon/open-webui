import { test, expect } from '@playwright/test';
import { login, SELECTORS } from '../helpers/auth';

test.describe('Authentication', () => {
	test('can log in and reach the home page', async ({ page }) => {
		// Act: login using the reusable helper
		await login(page);

		// Assert: chat input is visible — proves we're on the home page
		const chatInput = page.locator(SELECTORS.chatInput);
		await expect(chatInput).toBeVisible();

		// Assert: URL should have navigated away from /auth
		expect(page.url()).not.toContain('/auth');

		// Capture screenshot for visual verification
		await page.screenshot({
			path: 'screenshots/login-success.png',
			fullPage: true
		});
	});
});

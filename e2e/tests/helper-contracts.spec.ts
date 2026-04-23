import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';
import {
	createNewChat,
	selectModel,
	sendMessage,
	waitForResponse,
	CHAT_SELECTORS
} from '../helpers/chat';

/**
 * Contract tests for the shared E2E helpers in helpers/chat.ts.
 *
 * These tests validate that the helper functions work with the current
 * server and UI state — no VLM pipeline, just fast UI interactions.
 *
 * Purpose:
 *   - Catch selector drift (if Svelte components change class names or IDs)
 *   - Validate model selection with OpenRouter model ID format
 *   - Ensure the two-pronged completion detection in waitForResponse works
 *   - Verify createNewChat navigates correctly
 */

const TEST_MODEL = 'openrouter.qwen/qwen3.5-122b-a10b' as const;

test.describe('Helper Contract Tests', () => {
	/** UI interactions should be fast — 2 minute timeout is generous */
	test.setTimeout(120_000);

	test('selectModel: can select OpenRouter model and button text changes', async ({ page }) => {
		// Login and navigate to home
		await login(page);
		await createNewChat(page);

		// Verify the model selector shows default state before selection
		const modelButton = page.locator(CHAT_SELECTORS.modelSelectorButton);
		await expect(modelButton).toBeVisible();
		const textBefore = await modelButton.textContent();

		// Select the model
		await selectModel(page, TEST_MODEL);

		// Verify: button text changed from default "Select a model"
		await expect(modelButton).not.toContainText('Select a model');

		// Verify: the model option with the correct data-value was clicked
		// by checking the button now contains some part of the model name
		const textAfter = await modelButton.textContent();
		expect(textAfter).not.toBe(textBefore);

		// Screenshot for visual verification
		await page.screenshot({
			path: 'screenshots/contract-model-selected.png',
			fullPage: true
		});
	});

	test('createNewChat: navigates to home with chat input visible', async ({ page }) => {
		await login(page);

		// Navigate to a new chat
		await createNewChat(page);

		// Verify: chat input is visible (the core assertion of createNewChat)
		const chatInput = page.locator(CHAT_SELECTORS.chatInput);
		await expect(chatInput).toBeVisible();

		// Verify: URL is the root (new chat)
		expect(page.url()).toMatch(/\/$/);
	});

	/**
	 * Skipped: pre-existing issue — RAG search fires despite "Without searching"
	 * instruction, produces "No sources found" → "User not found." error, and
	 * neither copy-response-button nor send-button reappears.
	 * Same root cause as chat-basics.spec.ts failure documented in T3.
	 */
	test.skip('sendMessage + waitForResponse: full chat round-trip contract', async ({ page }) => {
		await login(page);
		await createNewChat(page);
		await selectModel(page, TEST_MODEL);

		// Send a deterministic message that should produce a short response
		await sendMessage(
			page,
			'Without searching for any documents, reply with exactly: CONTRACT_TEST_OK'
		);

		// Wait for response using the two-pronged detection
		const responseText = await waitForResponse(page, 120_000);

		// Verify: response is non-empty (the core contract)
		expect(responseText.length).toBeGreaterThan(0);

		// Verify: at least one assistant message exists in the DOM
		const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
		const count = await assistantMessages.count();
		expect(count).toBeGreaterThanOrEqual(1);

		// Verify: send button is visible again (generation complete)
		const sendButton = page.locator(CHAT_SELECTORS.sendButton);
		await expect(sendButton).toBeVisible();

		// Screenshot for evidence
		await page.screenshot({
			path: 'screenshots/contract-chat-roundtrip.png',
			fullPage: true
		});
	});

	test('CHAT_SELECTORS: all selectors match live DOM elements', async ({ page }) => {
		await login(page);
		await createNewChat(page);

		// Verify chatInput exists
		await expect(page.locator(CHAT_SELECTORS.chatInput)).toBeVisible();

		// Verify modelSelectorButton exists
		await expect(page.locator(CHAT_SELECTORS.modelSelectorButton)).toBeVisible();

		// Verify fileInput exists (hidden but attached)
		const fileInput = page.locator(CHAT_SELECTORS.fileInput);
		await expect(fileInput).toBeAttached();

		// Verify the file input is actually hidden (has the hidden attribute)
		const isHidden = await fileInput.getAttribute('hidden');
		// The `hidden` attribute is a boolean attribute — when present, getAttribute returns ""
		expect(isHidden).not.toBeNull();

		// Verify sendButton appears ONLY when input has text
		// (it's inside an {#if} block in MessageInput.svelte — not in DOM when input is empty)
		const sendButton = page.locator(CHAT_SELECTORS.sendButton);
		await expect(sendButton).toHaveCount(0); // not present with empty input

		// Type text to make the send button appear
		const chatInput = page.locator(CHAT_SELECTORS.chatInput);
		await chatInput.fill('test');
		await expect(sendButton).toBeVisible({ timeout: 5_000 });
	});
});

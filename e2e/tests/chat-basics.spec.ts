import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';
import { selectModel, sendMessage, waitForResponse } from '../helpers/chat';

/**
 * Model for chat-basics tests — OpenRouter cloud API for reliable availability.
 * This test verifies the chat UI flow, not model quality — reliability matters most.
 */
const TEST_MODEL = 'openrouter.qwen/qwen3.5-122b-a10b' as const;

test.describe('Chat Basics', () => {
	/** LLM inference can take 60-120s+ depending on load — use generous timeout */
	test.setTimeout(180_000);

	test('can select a model, send a message, and receive a response', async ({ page }) => {
		// Step 1: Log in using the reusable auth helper
		await login(page);

		// Step 2: Select the test model
		await selectModel(page, TEST_MODEL);

		// Step 3: Send a simple message that won't trigger RAG search
		// "Say hello" triggers RAG → "No sources found" → empty response → no copy button → timeout.
		// A direct, self-contained instruction avoids RAG query generation entirely.
		await sendMessage(
			page,
			'Without searching for any documents, reply with exactly the word: hello'
		);

		// Step 4: Wait for the assistant to finish responding
		const responseText = await waitForResponse(page, 120_000);

		// Step 5: Assert response contains meaningful content (not empty)
		expect(responseText.length).toBeGreaterThan(0);

		// Step 6: Capture screenshot for visual verification
		await page.screenshot({
			path: 'screenshots/chat-basics-response.png',
			fullPage: true
		});
	});
});

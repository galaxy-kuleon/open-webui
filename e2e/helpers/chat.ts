import { type Page, expect } from '@playwright/test';

/**
 * Chat interaction helpers for Open WebUI E2E tests.
 *
 * Design: pure functions operating on Page data — no classes, no shared mutable state.
 * Each function takes a Page as first arg, performs an action, returns void or data.
 * Follows the same conventions as auth.ts: named exports, `as const` selectors, camelCase functions.
 */

/** Selectors — single source of truth, derived from live UI component inspection */
export const CHAT_SELECTORS = {
	/** Model selector trigger button on the home/chat page */
	modelSelectorButton: '#model-selector-0-button',
	/** Search input inside the model dropdown */
	modelSearchInput: '#model-search-input',
	/** Model option items in dropdown — use [data-value="modelId"] to target specific model */
	modelOption: 'button[role="option"]',
	/** Chat message input textarea */
	chatInput: '#chat-input',
	/** Send message button */
	sendButton: '#send-message-button',
	/** Assistant response container — rendered as class="chat-assistant ..." */
	assistantMessage: '.chat-assistant',
	/** Copy button appears only when message.done is true — reliable completion indicator */
	copyResponseButton: '.copy-response-button',
	/**
	 * Hidden file input in MessageInput.svelte — the main file upload input.
	 * Uses [hidden] attribute (not style="display:none") which distinguishes it from #camera-input.
	 */
	fileInput: 'input[type="file"][hidden]'
} as const;

/**
 * Select a model from the model selector dropdown.
 *
 * Flow: click trigger button -> wait for dropdown -> type search -> click matching option
 *
 * @param page - Playwright Page instance
 * @param modelId - The model ID to select (e.g. "openrouter.qwen/qwen3.5-122b-a10b")
 */
export async function selectModel(page: Page, modelId: string): Promise<void> {
	// Click the model selector to open the dropdown
	await page.locator(CHAT_SELECTORS.modelSelectorButton).click();

	// Wait for the search input to appear (dropdown is open)
	const searchInput = page.locator(CHAT_SELECTORS.modelSearchInput);
	await searchInput.waitFor({ state: 'visible', timeout: 10_000 });

	// Type the model name to filter — use a portion of the ID for fuzzy search
	await searchInput.fill(modelId);

	// Wait for filtered results and click the matching option
	const modelOption = page.locator(`${CHAT_SELECTORS.modelOption}[data-value="${modelId}"]`);
	await modelOption.waitFor({ state: 'visible', timeout: 10_000 });
	await modelOption.click();

	// Verify dropdown closed by checking the trigger button text changed
	// (it now shows the model name instead of "Select a model")
	await expect(page.locator(CHAT_SELECTORS.modelSelectorButton)).not.toContainText(
		'Select a model',
		{ timeout: 5_000 }
	);
}

/**
 * Navigate to a new chat.
 *
 * Uses direct navigation to "/" rather than UI button click,
 * because the sidebar may be collapsed at test viewport (1280px).
 *
 * @param page - Playwright Page instance
 */
export async function createNewChat(page: Page): Promise<void> {
	await page.goto('/');
	await page.locator(CHAT_SELECTORS.chatInput).waitFor({
		state: 'visible',
		timeout: 30_000
	});
}

/**
 * Send a message in the chat.
 *
 * Flow: focus chat input -> fill text -> click send button
 *
 * @param page - Playwright Page instance
 * @param text - The message text to send
 */
export async function sendMessage(page: Page, text: string): Promise<void> {
	const chatInput = page.locator(CHAT_SELECTORS.chatInput);
	await chatInput.waitFor({ state: 'visible', timeout: 10_000 });
	await chatInput.fill(text);

	// Click send button (it enables once input is non-empty)
	const sendButton = page.locator(CHAT_SELECTORS.sendButton);
	await sendButton.click();
}

/**
 * Upload a file to the chat via the hidden file input.
 *
 * Mechanism: Playwright's setInputFiles triggers the hidden <input type="file"> element
 * in MessageInput.svelte, which fires the change event → inputFilesHandler → uploadFileHandler
 * → POST /api/v1/files/ → FileItem renders with loading=false when complete.
 *
 * Completion detection strategy (two-pronged):
 * 1. Wait for the /api/v1/files/ response (network-level confirmation)
 * 2. Wait for a FileItem button with the filename to appear and stop showing a spinner
 *
 * @param page - Playwright Page instance
 * @param filePath - Absolute path to the file on disk
 * @param timeout - Max time to wait for upload completion (default: 30s)
 */
export async function uploadFile(
	page: Page,
	filePath: string,
	timeout: number = 30_000
): Promise<void> {
	// Extract filename for assertion — the FileItem displays the decoded filename
	const fileName = filePath.split('/').pop() ?? filePath;

	// Start listening for the upload API response BEFORE triggering the upload
	const uploadResponsePromise = page.waitForResponse(
		(resp) => resp.url().includes('/api/v1/files/') && resp.request().method() === 'POST',
		{ timeout }
	);

	// Set the file on the hidden input — triggers change event and upload flow
	const fileInput = page.locator(CHAT_SELECTORS.fileInput);
	await fileInput.setInputFiles(filePath);

	// Wait for the API response — confirms server-side upload is done
	await uploadResponsePromise;

	// Wait for the FileItem to appear with the filename visible
	// FileItem (small mode) renders as <button class="relative group ..."> containing filename text
	const fileItem = page.locator('button.relative.group').filter({
		hasText: fileName
	});
	await fileItem.first().waitFor({ state: 'visible', timeout });

	// Wait for spinner to disappear — Spinner.svelte renders an SVG with class "spinner_ajPY"
	// When upload finishes, status changes to 'uploaded' and loading=false removes the Spinner
	await expect(fileItem.first().locator('.spinner_ajPY')).toHaveCount(0, {
		timeout
	});
}

/**
 * Trigger an agent skill by sending a message with the skill name keyword,
 * then wait for evidence that the skill execution has STARTED.
 *
 * Detection strategy:
 * The middleware emits status events via SSE when it intercepts a skill keyword.
 * The first visible signal is a .status-description element inside .chat-assistant
 * containing either "Analyzing chat context" (extracting_params) or
 * "Running agent skill" (start sub_action).
 *
 * This does NOT wait for skill completion — only confirms execution began.
 *
 * @param page - Playwright Page instance
 * @param message - The message text (must contain the skill name for keyword intercept)
 * @param timeout - Max time to wait for skill start signal (default: 300s — skill init is slow)
 */
export async function triggerSkillAndWaitForStart(
	page: Page,
	message: string,
	timeout: number = 300_000
): Promise<void> {
	// Send the message containing the skill keyword
	await sendMessage(page, message);

	// Wait for the assistant message container to appear (SSE streaming started)
	const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
	await assistantMessages.first().waitFor({ state: 'visible', timeout });

	// Wait for a skill-related status indicator to appear.
	// The middleware emits status events with action="agent_skill".
	// In the DOM, these render inside .status-description elements.
	// We look for any of the known early status texts:
	//   - "Analyzing chat context" (extracting_params — appears first)
	//   - "Running agent skill" (start — appears second)
	//   - "Thinking" (thinking — appears during skill execution)
	//   - "Output" (output — appears if skill produces output quickly)
	const skillStatusIndicator = assistantMessages
		.last()
		.locator('.status-description')
		.filter({
			hasText: /Analyzing chat context|Running agent skill|Thinking|Output/
		});

	await skillStatusIndicator.first().waitFor({ state: 'visible', timeout });
}

/**
 * Wait for the assistant to finish generating a response.
 *
 * Detection strategy (two-pronged race):
 * 1. Primary: .copy-response-button appears (message.done = true, content exists)
 * 2. Fallback: the send button (#send-message-button) reappears after generation
 *    This covers edge cases where the model responds with no text content
 *    (e.g., RAG returns "No sources found" without generating actual text).
 *
 * Returns the text content of the last assistant message (may be empty in edge cases).
 *
 * @param page - Playwright Page instance
 * @param timeout - Max time to wait for response completion (default: 120s for LLM inference)
 */
export async function waitForResponse(page: Page, timeout: number = 120_000): Promise<string> {
	// Wait for at least one assistant message to appear (streaming started)
	const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
	await assistantMessages.first().waitFor({ state: 'visible', timeout });

	// Wait for the send button to disappear first — this confirms generation is active.
	// In MessageInput.svelte, the send button (#send-message-button) is in an {#if}/{:else}
	// block with the stop button. During generation, the send button is removed from the DOM.
	// We must confirm it's gone BEFORE watching for its return, to avoid false positives.
	const sendButton = page.locator(CHAT_SELECTORS.sendButton);
	await sendButton.waitFor({ state: 'hidden', timeout: 30_000 });

	// Now race two completion signals:
	// 1. Copy button appears (normal: model generated content, message.done = true)
	// 2. Send button reappears (fallback: generation ended, even if no content was produced)
	//    When message.done becomes true, the stop button is replaced by the send button.
	const copyButton = page.locator(CHAT_SELECTORS.copyResponseButton);

	await Promise.race([
		copyButton.last().waitFor({ state: 'visible', timeout }),
		sendButton.waitFor({ state: 'visible', timeout })
	]);

	// Extract the text from the last assistant message
	const lastAssistant = assistantMessages.last();
	const text = (await lastAssistant.textContent()) ?? '';
	return text.trim();
}

/**
 * Wait for an agent skill to fully complete (not just start).
 *
 * Detection strategy:
 * The skill completion emits a status event with sub_action="complete" and done=true.
 * On the frontend, the final assistant message will contain "Output files:" text
 * followed by markdown download links (rendered as <a> tags pointing to /api/v1/files/).
 *
 * We use a two-pronged approach:
 * 1. Primary: Wait for "Output files" text to appear in the last assistant message
 *    (this is the most reliable signal — builtin.py appends this when file_refs exist)
 * 2. Fallback: Wait for the copy-response-button (message.done = true) which means
 *    the entire response generation including tool calls has finished
 *
 * Returns the full text content of the last assistant message for assertion.
 *
 * @param page - Playwright Page instance
 * @param timeout - Max time to wait for skill completion (default: 900s / 15 min for VLM pipelines)
 */
export async function waitForSkillCompletion(
	page: Page,
	timeout: number = 900_000
): Promise<string> {
	const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);

	// Wait for at least one assistant message
	await assistantMessages.first().waitFor({ state: 'visible', timeout: 60_000 });

	// Race two completion signals:
	// 1. "Output files" text appears (skill produced files and they were uploaded)
	// 2. Copy button appears on the final assistant message (generation fully done)
	//
	// We poll for "Output files" as the primary signal because skills may produce
	// multiple assistant messages (status updates, then final content).
	const outputFilesLocator = assistantMessages.last().getByText('Output files');
	const copyButton = page.locator(CHAT_SELECTORS.copyResponseButton).last();

	await Promise.race([
		outputFilesLocator.waitFor({ state: 'visible', timeout }),
		copyButton.waitFor({ state: 'visible', timeout })
	]);

	// Give a moment for any final rendering to settle
	await page.waitForTimeout(2_000);

	// Extract text from the last assistant message
	const lastAssistant = assistantMessages.last();
	const text = (await lastAssistant.textContent()) ?? '';
	return text.trim();
}

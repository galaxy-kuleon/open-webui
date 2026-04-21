import { test, expect } from '@playwright/test';
import { loginAsAdmin, getRAGConfigViaAPI, updateRAGConfigViaAPI } from '../helpers/admin';
import { selectModel, uploadFile, CHAT_SELECTORS } from '../helpers/chat';

/**
 * Image Upload Gate — 2×2 quadrant smoke tests.
 *
 * The gate lives at MessageInput.svelte:744-760:
 *   if image file AND no vision-capable model AND !imageAnalysisEnabled → block (toast.error + return)
 *
 * Quadrant matrix:
 *   Q1: vision model    + IMAGE_ANALYSIS_ENABLED=true  → upload succeeds (file attached)
 *   Q2: vision model    + IMAGE_ANALYSIS_ENABLED=false → upload succeeds (vision bypasses gate)
 *   Q3: non-vision model + IMAGE_ANALYSIS_ENABLED=true  → upload allowed (toast.info, file attached)
 *   Q4: non-vision model + IMAGE_ANALYSIS_ENABLED=false → upload BLOCKED (toast.error, no file)
 *
 * Model defaults — override via environment variables if these models are not configured
 * in your Open WebUI instance:
 *
 *   TEST_VISION_MODEL:     a vision-capable model (meta.capabilities.vision = true).
 *                          Default: "openrouter.anthropic/claude-3-5-sonnet"
 *                          Override: TEST_VISION_MODEL=your.model/id bun run test:e2e
 *
 *   TEST_NON_VISION_MODEL: a text-only model (meta.capabilities.vision = false or absent).
 *                          Default: "openrouter.qwen/qwen3.5-122b-a10b" (same as file-upload.spec.ts)
 *                          Override: TEST_NON_VISION_MODEL=your.model/id bun run test:e2e
 *
 * Image fixture:
 *   Uses static/favicon.png from the Open WebUI repo (always present — no external dependency).
 *   Override: TEST_IMAGE_PATH=/path/to/your/image.png bun run test:e2e
 */
const TEST_VISION_MODEL = process.env.TEST_VISION_MODEL ?? 'openrouter.anthropic/claude-3-5-sonnet';

const TEST_NON_VISION_MODEL =
	process.env.TEST_NON_VISION_MODEL ?? 'openrouter.qwen/qwen3.5-122b-a10b';

/**
 * Absolute path to an image fixture present in the repo.
 * static/favicon.png is a 16×16 PNG — minimal, always present, zero external dependency.
 */
const TEST_IMAGE_PATH =
	process.env.TEST_IMAGE_PATH ?? '/Users/noelbao/Works/open-webui/static/favicon.png';

/**
 * The error toast text rendered by svelte-sonner when the gate blocks an image upload.
 * Source: MessageInput.svelte:750-754 → toast.error($i18n.t('Image analysis is not enabled...'))
 * The UI runs in en-US locale (set in auth.ts addInitScript) so the key maps to English.
 */
const BLOCKED_TOAST_TEXT =
	'Image analysis is not enabled. Select a vision-capable model or ask your administrator to enable image analysis.';

/**
 * The info toast text shown when image analysis IS enabled but vision model is not selected.
 * Source: MessageInput.svelte:759 → toast.info($i18n.t('Images will be analyzed as text...'))
 */
const NON_VISION_ALLOWED_TOAST_TEXT = 'Images will be analyzed as text for selected model(s)';

test.describe('Image Upload Gate — 2×2 matrix', () => {
	/** Per-test: capture the original IMAGE_ANALYSIS_ENABLED to restore after */
	let originalImageAnalysisEnabled: unknown;

	test.beforeEach(async ({ page }) => {
		await loginAsAdmin(page);
		const config = await getRAGConfigViaAPI(page);
		originalImageAnalysisEnabled = config.IMAGE_ANALYSIS_ENABLED;
	});

	test.afterEach(async ({ page }) => {
		// Restore IMAGE_ANALYSIS_ENABLED to its pre-test value.
		// This pairs with the beforeEach capture — every test that mutates the config
		// is covered by this restoration regardless of test pass/fail.
		await updateRAGConfigViaAPI(page, {
			IMAGE_ANALYSIS_ENABLED: originalImageAnalysisEnabled
		});
	});

	// ---------------------------------------------------------------------------
	// Q1: Vision model + IMAGE_ANALYSIS_ENABLED=true → upload succeeds
	// ---------------------------------------------------------------------------
	test('vision model with analysis enabled — image upload succeeds and file attaches', async ({
		page
	}) => {
		// Enable image analysis
		await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: true });

		// Select a vision-capable model — visionCapableModels.length > 0 → gate is skipped entirely
		await selectModel(page, TEST_VISION_MODEL);

		// Upload the image — uses the shared helper which asserts file attaches + no spinner
		await uploadFile(page, TEST_IMAGE_PATH);

		// Confirm the file item is visible (uploadFile already asserts this, explicit for clarity)
		const fileName = TEST_IMAGE_PATH.split('/').pop() ?? TEST_IMAGE_PATH;
		const fileItem = page.locator('button.relative.group').filter({ hasText: fileName });
		await expect(fileItem.first()).toBeVisible();
	});

	// ---------------------------------------------------------------------------
	// Q2: Vision model + IMAGE_ANALYSIS_ENABLED=false → upload still succeeds
	//     Gate at line 745 only fires when visionCapableModels.length === 0;
	//     a vision model means the gate is never reached regardless of the flag.
	// ---------------------------------------------------------------------------
	test('vision model with analysis disabled — image upload still succeeds (gate bypassed)', async ({
		page
	}) => {
		// Disable image analysis
		await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: false });

		// Select a vision-capable model — gate is never entered
		await selectModel(page, TEST_VISION_MODEL);

		// Upload should succeed
		await uploadFile(page, TEST_IMAGE_PATH);

		const fileName = TEST_IMAGE_PATH.split('/').pop() ?? TEST_IMAGE_PATH;
		const fileItem = page.locator('button.relative.group').filter({ hasText: fileName });
		await expect(fileItem.first()).toBeVisible();
	});

	// ---------------------------------------------------------------------------
	// Q3: Non-vision model + IMAGE_ANALYSIS_ENABLED=true → upload allowed
	//     Gate fires (no vision model), imageAnalysisEnabled is true → toast.info + continues
	// ---------------------------------------------------------------------------
	test('non-vision model with analysis enabled — image upload allowed with info toast', async ({
		page
	}) => {
		// Enable image analysis
		await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: true });

		// Select a non-vision model — visionCapableModels.length === 0
		await selectModel(page, TEST_NON_VISION_MODEL);

		// Upload should proceed (gate allows it, shows info toast)
		await uploadFile(page, TEST_IMAGE_PATH);

		const fileName = TEST_IMAGE_PATH.split('/').pop() ?? TEST_IMAGE_PATH;
		const fileItem = page.locator('button.relative.group').filter({ hasText: fileName });
		await expect(fileItem.first()).toBeVisible();

		// Verify the info toast appeared — confirms the gate branch was exercised
		// svelte-sonner renders toasts as [data-sonner-toast] elements
		const infoToast = page
			.locator('[data-sonner-toast]')
			.filter({ hasText: NON_VISION_ALLOWED_TOAST_TEXT });
		await expect(infoToast.first()).toBeVisible({ timeout: 5_000 });
	});

	// ---------------------------------------------------------------------------
	// Q4: Non-vision model + IMAGE_ANALYSIS_ENABLED=false → upload BLOCKED
	//     Gate fires (no vision model), imageAnalysisEnabled is false →
	//     toast.error + return — the upload API is never called.
	//
	//     IMPORTANT: Do NOT use uploadFile() here — it awaits the /api/v1/files/ POST
	//     response which will never arrive (the return statement at line 755 prevents
	//     the upload from reaching the network). Instead, trigger the file input
	//     directly and assert the negative outcome.
	// ---------------------------------------------------------------------------
	test('non-vision model with analysis disabled — image upload is blocked by gate', async ({
		page
	}) => {
		// Disable image analysis
		await updateRAGConfigViaAPI(page, { IMAGE_ANALYSIS_ENABLED: false });

		// Select a non-vision model — visionCapableModels.length === 0
		await selectModel(page, TEST_NON_VISION_MODEL);

		// Set the file on the hidden input directly — this fires the change event and
		// triggers the inputFilesHandler in MessageInput.svelte, which runs the gate check.
		// The gate returns early (line 755) before reaching any network call.
		const fileInput = page.locator(CHAT_SELECTORS.fileInput);
		await fileInput.setInputFiles(TEST_IMAGE_PATH);

		// Assert the error toast appears — this is the only UI signal from the blocked path
		const errorToast = page.locator('[data-sonner-toast]').filter({ hasText: BLOCKED_TOAST_TEXT });
		await expect(errorToast.first()).toBeVisible({ timeout: 5_000 });

		// Assert no file item rendered — the upload was blocked before any API call
		const fileName = TEST_IMAGE_PATH.split('/').pop() ?? TEST_IMAGE_PATH;
		const fileItem = page.locator('button.relative.group').filter({ hasText: fileName });
		await expect(fileItem).toHaveCount(0);
	});
});

import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';
import { selectModel, uploadFile } from '../helpers/chat';

/**
 * Model for file upload tests — OpenRouter cloud API for reliable availability.
 * File upload test only needs model selection to unlock the upload UI.
 */
const TEST_MODEL = 'openrouter.qwen/qwen3.5-122b-a10b' as const;

/** Test PDF file — English contract, 93KB */
const TEST_PDF_PATH = '/Users/noelbao/Works/glm-ocr-latest-test/contract_en3.pdf' as const;
const TEST_PDF_FILENAME = 'contract_en3.pdf' as const;

test.describe('File Upload', () => {
	/** File upload + processing can take time */
	test.setTimeout(120_000);

	test('can upload a PDF file and see it attached in the chat input', async ({ page }) => {
		// Step 1: Log in
		await login(page);

		// Step 2: Select the test model (required for file upload — fileUploadCapableModels check)
		await selectModel(page, TEST_MODEL);

		// Step 3: Upload the PDF file
		await uploadFile(page, TEST_PDF_PATH);

		// Step 4: Assert the file appears as attached — FileItem with filename visible
		const fileItem = page.locator('button.relative.group').filter({
			hasText: TEST_PDF_FILENAME
		});
		await expect(fileItem.first()).toBeVisible();

		// Step 5: Assert the filename text is rendered correctly
		await expect(fileItem.first()).toContainText(TEST_PDF_FILENAME);

		// Step 6: Assert no spinner — upload is complete
		await expect(fileItem.first().locator('.spinner_ajPY')).toHaveCount(0);

		// Step 7: Assert the dismiss/remove button exists — confirms FileItem rendered with dismissible=true
		const removeButton = fileItem.first().locator('button[aria-label="Remove File"]');
		await expect(removeButton).toBeAttached();

		// Step 8: Capture screenshot showing the attached file
		await page.screenshot({
			path: 'screenshots/file-upload-attached.png',
			fullPage: true
		});
	});
});

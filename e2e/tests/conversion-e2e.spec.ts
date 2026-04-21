import { test, expect } from '@playwright/test';
import { login } from '../helpers/auth';
import {
	createNewChat,
	selectModel,
	uploadFile,
	triggerSkillAndWaitForStart,
	waitForSkillCompletion,
	CHAT_SELECTORS
} from '../helpers/chat';
import { zipContainsEntry, extractDocxText, countMarkerMatches } from '../helpers/docx';

/**
 * E2E test: Validate the full PDF → DOCX conversion quality through the
 * anything-to-docx agent skill.
 *
 * Unlike skill-output.spec.ts (which focuses on output FILTERING — no intermediates),
 * this test validates CONVERSION QUALITY:
 *   1. Skill triggers and completes without error
 *   2. Output contains a .docx download link
 *   3. Downloaded DOCX has PK zip header (valid OOXML)
 *   4. DOCX file size is within reasonable bounds (>1KB, <50MB)
 *   5. DOCX contains word/document.xml (core OOXML content part)
 *   6. No intermediate files leak into output
 *
 * Parameterized over two test PDFs:
 *   - contract_en3.pdf (93KB, English, 3 pages) — Latin script baseline
 *   - contract_zh3.pdf (226KB, Chinese, 4 pages) — CJK content
 */

const TEST_MODEL = 'openrouter.qwen/qwen3.5-122b-a10b' as const;
const SKILL_TRIGGER_MESSAGE = 'convert this file to docx using anything-to-docx' as const;

/** PK zip magic bytes — DOCX is OOXML = zip archive */
const PK_ZIP_MAGIC = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

/** DOCX size bounds — a real conversion should produce between 1KB and 50MB */
const MIN_DOCX_SIZE = 1_024; // 1KB
const MAX_DOCX_SIZE = 50 * 1_024 * 1_024; // 50MB

/** Test cases — plain data, including expected content markers for text verification */
const TEST_PDFS = [
	{
		label: 'EN',
		path: '/Users/noelbao/Works/glm-ocr-latest-test/contract_en3.pdf',
		description: 'English contract (93KB, 3 pages)',
		/** Distinctive strings from the source PDF — at least some must survive conversion */
		contentMarkers: [
			'AG-2025-TW-0042',
			'Artificial Intelligence',
			'Party A',
			'Party B',
			'Taiwan Smart Technology'
		],
		/** Minimum number of markers that must be found (accounts for OCR/VLM variance) */
		minMarkerMatches: 3
	},
	{
		label: 'ZH',
		path: '/Users/noelbao/Works/glm-ocr-latest-test/contract_zh3.pdf',
		description: 'Chinese contract (226KB, 4 pages)',
		contentMarkers: ['AG-2025-TW-0042', '人工智慧', '甲方', '乙方', '台灣智慧科技'],
		minMarkerMatches: 3
	}
] as const;

test.describe.serial('PDF to DOCX Conversion Quality', () => {
	// 15-minute timeout per test — VLM pipeline through OpenRouter is slow
	test.setTimeout(900_000);

	for (const pdf of TEST_PDFS) {
		test(`conversion quality [${pdf.label}]: ${pdf.description}`, async ({ page }) => {
			const screenshotPrefix = `screenshots/conversion-e2e-${pdf.label.toLowerCase()}`;

			// === Step 1: Login ===
			await login(page);
			await page.screenshot({
				path: `${screenshotPrefix}-01-logged-in.png`,
				fullPage: true
			});

			// === Step 2: Start a fresh chat and select model ===
			await createNewChat(page);
			await selectModel(page, TEST_MODEL);
			await page.screenshot({
				path: `${screenshotPrefix}-02-model-selected.png`,
				fullPage: true
			});

			// === Step 3: Upload the test PDF ===
			await uploadFile(page, pdf.path);
			await page.screenshot({
				path: `${screenshotPrefix}-03-file-uploaded.png`,
				fullPage: true
			});

			// === Step 4: Trigger the skill and wait for it to start ===
			await triggerSkillAndWaitForStart(page, SKILL_TRIGGER_MESSAGE);
			await page.screenshot({
				path: `${screenshotPrefix}-04-skill-started.png`,
				fullPage: true
			});

			// === Step 5: Wait for skill to fully complete ===
			const responseText = await waitForSkillCompletion(page);
			await page.screenshot({
				path: `${screenshotPrefix}-05-skill-complete.png`,
				fullPage: true
			});

			// === Step 6: Verify "Output files" section exists ===
			expect(
				responseText,
				`[${pdf.label}] Response should contain "Output files" section`
			).toContain('Output files');

			// === Step 7: Verify no error indicators in response ===
			// Skills report errors via "Error:" or "failed" in the response text
			const lowerResponse = responseText.toLowerCase();
			expect(
				lowerResponse.includes('error:') || lowerResponse.includes('skill failed'),
				`[${pdf.label}] Response should not indicate a skill error. Response: ${responseText.slice(0, 500)}`
			).toBe(false);

			// === Step 8: Verify a .docx download link exists ===
			const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
			const lastAssistant = assistantMessages.last();

			const docxLinks = lastAssistant.locator('a[href*="/api/v1/files/"]').filter({
				hasText: /\.docx$/i
			});
			const docxLinkCount = await docxLinks.count();
			expect(
				docxLinkCount,
				`[${pdf.label}] Expected at least one .docx download link`
			).toBeGreaterThanOrEqual(1);

			// === Step 9: Verify no intermediate file links ===
			const intermediateExts = ['.xml', '.json', '.png', '.jpg', '.jpeg', '.bmp', '.txt', '.md'];
			for (const ext of intermediateExts) {
				const badLinks = lastAssistant
					.locator('a[href*="/api/v1/files/"]')
					.filter({ hasText: new RegExp(`\\${ext}$`, 'i') });
				const count = await badLinks.count();
				expect(count, `[${pdf.label}] No ${ext} intermediate files should appear in output`).toBe(
					0
				);
			}

			// === Step 10: Download and validate the DOCX ===
			const firstDocxLink = docxLinks.first();
			const href = await firstDocxLink.getAttribute('href');
			expect(href, `[${pdf.label}] DOCX link should have an href`).toBeTruthy();

			const downloadResponse = await page.request.get(href!);
			expect(
				downloadResponse.ok(),
				`[${pdf.label}] DOCX download should succeed (got ${downloadResponse.status()})`
			).toBe(true);

			const fileBuffer = await downloadResponse.body();

			// --- 10a: PK zip header check ---
			const header = new Uint8Array(fileBuffer.slice(0, 4));
			const hasPKHeader =
				header[0] === PK_ZIP_MAGIC[0] &&
				header[1] === PK_ZIP_MAGIC[1] &&
				header[2] === PK_ZIP_MAGIC[2] &&
				header[3] === PK_ZIP_MAGIC[3];
			expect(
				hasPKHeader,
				`[${pdf.label}] DOCX must start with PK zip header (50 4B 03 04), got: ${Array.from(header)
					.map((b) => b.toString(16).padStart(2, '0'))
					.join(' ')}`
			).toBe(true);

			// --- 10b: File size within bounds ---
			expect(
				fileBuffer.length,
				`[${pdf.label}] DOCX size (${fileBuffer.length} bytes) should be > ${MIN_DOCX_SIZE} bytes`
			).toBeGreaterThan(MIN_DOCX_SIZE);
			expect(
				fileBuffer.length,
				`[${pdf.label}] DOCX size (${fileBuffer.length} bytes) should be < ${MAX_DOCX_SIZE} bytes`
			).toBeLessThan(MAX_DOCX_SIZE);

			// --- 10c: Contains word/document.xml (core OOXML content) ---
			const hasDocumentXml = zipContainsEntry(fileBuffer, 'word/document.xml');
			expect(
				hasDocumentXml,
				`[${pdf.label}] DOCX zip must contain word/document.xml (core OOXML content part)`
			).toBe(true);

			// --- 10d: Contains [Content_Types].xml (OOXML package descriptor) ---
			const hasContentTypes = zipContainsEntry(fileBuffer, '[Content_Types].xml');
			expect(
				hasContentTypes,
				`[${pdf.label}] DOCX zip must contain [Content_Types].xml (OOXML package descriptor)`
			).toBe(true);

			// --- 10e: Text content verification — the DOCX must contain real text from the source PDF ---
			const docxText = await extractDocxText(fileBuffer);
			expect(
				docxText.length,
				`[${pdf.label}] DOCX document.xml should contain non-trivial text content (got ${docxText.length} chars)`
			).toBeGreaterThan(100);

			const { count, matched, missed } = countMarkerMatches(docxText, pdf.contentMarkers);
			expect(
				count,
				`[${pdf.label}] DOCX should contain at least ${pdf.minMarkerMatches}/${pdf.contentMarkers.length} content markers. ` +
					`Matched: [${matched.join(', ')}]. Missed: [${missed.join(', ')}]. ` +
					`Text sample: "${docxText.slice(0, 200)}"`
			).toBeGreaterThanOrEqual(pdf.minMarkerMatches);

			// === Step 11: Final screenshot ===
			await page.screenshot({
				path: `${screenshotPrefix}-06-verified.png`,
				fullPage: true
			});
		});
	}
});

/**
 * Unit-level tests for helper functions — no browser needed.
 *
 * These validate that countMarkerMatches and extractDocxText produce
 * useful diagnostic output when content doesn't match, per T2 evaluator
 * recommendation. Fast, free (no OpenRouter), and regression-safe.
 */
test.describe('Helper diagnostics', () => {
	test('countMarkerMatches: reports matched and missed markers accurately', () => {
		const text =
			'This contract between Party A and Party B covers Artificial Intelligence services.';
		const markers = [
			'Party A',
			'Party B',
			'Artificial Intelligence',
			'Taiwan Smart Technology',
			'AG-2025-TW-0042'
		];

		const result = countMarkerMatches(text, markers);

		// Verify counts
		expect(result.count).toBe(3);
		expect(result.matched).toEqual(['Party A', 'Party B', 'Artificial Intelligence']);
		expect(result.missed).toEqual(['Taiwan Smart Technology', 'AG-2025-TW-0042']);

		// Verify the diagnostic message that would be constructed in the assertion
		const diagMsg = `Matched: [${result.matched.join(', ')}]. Missed: [${result.missed.join(', ')}].`;
		expect(diagMsg).toContain('Taiwan Smart Technology');
		expect(diagMsg).toContain('AG-2025-TW-0042');
	});

	test('countMarkerMatches: case-insensitive matching works', () => {
		const text = 'PARTY A and party b in a CONTRACT';
		const markers = ['Party A', 'Party B', 'Contract'];

		const result = countMarkerMatches(text, markers);

		expect(result.count).toBe(3);
		expect(result.missed).toEqual([]);
	});

	test('countMarkerMatches: empty text returns all markers as missed', () => {
		const markers = ['AG-2025', 'Party A'];
		const result = countMarkerMatches('', markers);

		expect(result.count).toBe(0);
		expect(result.missed).toEqual(['AG-2025', 'Party A']);
		expect(result.matched).toEqual([]);
	});

	test('countMarkerMatches: empty markers returns zero count', () => {
		const result = countMarkerMatches('some text content', []);

		expect(result.count).toBe(0);
		expect(result.matched).toEqual([]);
		expect(result.missed).toEqual([]);
	});

	test('extractDocxText: returns empty string for non-DOCX buffer', async () => {
		// A buffer that is a valid zip but has no word/document.xml
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		zip.file('dummy.txt', 'not a docx');
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toBe('');
	});

	test('extractDocxText: extracts text from minimal DOCX-like zip', async () => {
		// Create a minimal zip with word/document.xml containing w:t tags
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		const minimalXml = `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Hello World AG-2025-TW-0042</w:t></w:r></w:p></w:body>
</w:document>`;
		zip.file('word/document.xml', minimalXml);
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toContain('Hello World');
		expect(text).toContain('AG-2025-TW-0042');
	});
});

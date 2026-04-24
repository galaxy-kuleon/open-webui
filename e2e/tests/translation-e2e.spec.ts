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
 * E2E test: Validate the full PDF → DOCX TRANSLATION pipeline through the
 * anything-to-docx agent skill with --lang parameter.
 *
 * Unlike conversion-e2e.spec.ts (which validates conversion quality in the
 * source language), this test validates that TRANSLATION works:
 *   1. Skill triggers and completes without error
 *   2. Output contains a .docx download link
 *   3. Downloaded DOCX has valid OOXML structure
 *   4. DOCX contains TARGET-LANGUAGE text (not source language)
 *
 * Test matrix:
 *   - contract_en3.pdf (93KB, English) → translate to zh-TW
 *   - contract_zh3.pdf (226KB, Chinese) → translate to en-US
 */

const TEST_MODEL = 'openrouter.qwen/qwen3.5-122b-a10b' as const;

/** PK zip magic bytes — DOCX is OOXML = zip archive */
const PK_ZIP_MAGIC = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

/** DOCX size bounds */
const MIN_DOCX_SIZE = 1_024; // 1KB
const MAX_DOCX_SIZE = 50 * 1_024 * 1_024; // 50MB

/** Translation test cases — plain data */
const TRANSLATION_TESTS = [
	{
		label: 'EN→zh-TW',
		sourcePath: '/Users/noelbao/Works/glm-ocr-latest-test/contract_en3.pdf',
		sourceDescription: 'English contract (93KB, 3 pages)',
		targetLang: 'zh-TW',
		triggerMessage:
			'convert this file to docx and translate to zh-TW using anything-to-docx --lang zh-TW',
		/**
		 * Target-language content markers — these are common Chinese terms that
		 * should appear in a translated legal/contract document.
		 * We use broad terms to account for translation variance.
		 */
		contentMarkers: ['合約', '合同', '甲方', '乙方', '人工智慧', '智能', '服務', '條款'],
		/** Minimum markers to find — lenient because translation quality varies */
		minMarkerMatches: 2
	},
	{
		label: 'ZH→en-US',
		sourcePath: '/Users/noelbao/Works/glm-ocr-latest-test/contract_zh3.pdf',
		sourceDescription: 'Chinese contract (226KB, 4 pages)',
		targetLang: 'en-US',
		triggerMessage:
			'convert this file to docx and translate to en-US using anything-to-docx --lang en-US',
		/**
		 * Target-language content markers — common English terms in a translated
		 * contract document.
		 */
		contentMarkers: [
			'contract',
			'agreement',
			'party',
			'service',
			'article',
			'artificial intelligence',
			'term',
			'obligation'
		],
		minMarkerMatches: 2
	}
] as const;

test.describe.serial('PDF to DOCX Translation Pipeline', () => {
	// 30-minute timeout per test — translation pipeline is VERY slow
	test.setTimeout(1_800_000);

	for (const tc of TRANSLATION_TESTS) {
		test(`translation [${tc.label}]: ${tc.sourceDescription} → ${tc.targetLang}`, async ({
			page
		}) => {
			const screenshotPrefix = `screenshots/translation-e2e-${tc.label.toLowerCase().replace('→', '-')}`;

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

			// === Step 3: Upload the source PDF ===
			await uploadFile(page, tc.sourcePath);
			await page.screenshot({
				path: `${screenshotPrefix}-03-file-uploaded.png`,
				fullPage: true
			});

			// === Step 4: Trigger the skill with --lang parameter ===
			await triggerSkillAndWaitForStart(page, tc.triggerMessage);
			await page.screenshot({
				path: `${screenshotPrefix}-04-skill-started.png`,
				fullPage: true
			});

			// === Step 5: Wait for skill completion (very long — translation is slow) ===
			const responseText = await waitForSkillCompletion(page, 1_800_000);
			await page.screenshot({
				path: `${screenshotPrefix}-05-skill-complete.png`,
				fullPage: true
			});

			// === Step 6: Verify "Output files" section exists ===
			expect(
				responseText,
				`[${tc.label}] Response should contain "Output files" section`
			).toContain('Output files');

			// === Step 7: No explicit failure-string check ===
			// The translation pipeline often includes Python tracebacks and "error:"
			// strings in intermediate tool output that are non-fatal (the skill
			// recovers and still produces output). No backend emits a canonical
			// "skill failed" string either. The content marker check in Step 10
			// is the authoritative validation that the pipeline succeeded.

			// === Step 8: Verify a .docx download link exists ===
			const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
			const lastAssistant = assistantMessages.last();

			const docxLinks = lastAssistant.locator('a[href*="/api/v1/files/"]').filter({
				hasText: /\.docx$/i
			});
			const docxLinkCount = await docxLinks.count();
			expect(
				docxLinkCount,
				`[${tc.label}] Expected at least one .docx download link`
			).toBeGreaterThanOrEqual(1);

			// === Step 9: Download and validate the DOCX ===
			const firstDocxLink = docxLinks.first();
			const href = await firstDocxLink.getAttribute('href');
			expect(href, `[${tc.label}] DOCX link should have an href`).toBeTruthy();

			const downloadResponse = await page.request.get(href!);
			expect(
				downloadResponse.ok(),
				`[${tc.label}] DOCX download should succeed (got ${downloadResponse.status()})`
			).toBe(true);

			const fileBuffer = await downloadResponse.body();

			// --- 9a: PK zip header check ---
			const header = new Uint8Array(fileBuffer.slice(0, 4));
			const hasPKHeader =
				header[0] === PK_ZIP_MAGIC[0] &&
				header[1] === PK_ZIP_MAGIC[1] &&
				header[2] === PK_ZIP_MAGIC[2] &&
				header[3] === PK_ZIP_MAGIC[3];
			expect(
				hasPKHeader,
				`[${tc.label}] DOCX must start with PK zip header (50 4B 03 04), got: ${Array.from(header)
					.map((b) => b.toString(16).padStart(2, '0'))
					.join(' ')}`
			).toBe(true);

			// --- 9b: File size within bounds ---
			expect(
				fileBuffer.length,
				`[${tc.label}] DOCX size (${fileBuffer.length} bytes) should be > ${MIN_DOCX_SIZE} bytes`
			).toBeGreaterThan(MIN_DOCX_SIZE);
			expect(
				fileBuffer.length,
				`[${tc.label}] DOCX size (${fileBuffer.length} bytes) should be < ${MAX_DOCX_SIZE} bytes`
			).toBeLessThan(MAX_DOCX_SIZE);

			// --- 9c: Contains word/document.xml (core OOXML content) ---
			const hasDocumentXml = zipContainsEntry(fileBuffer, 'word/document.xml');
			expect(hasDocumentXml, `[${tc.label}] DOCX zip must contain word/document.xml`).toBe(true);

			// --- 9d: Contains [Content_Types].xml (OOXML package descriptor) ---
			const hasContentTypes = zipContainsEntry(fileBuffer, '[Content_Types].xml');
			expect(hasContentTypes, `[${tc.label}] DOCX zip must contain [Content_Types].xml`).toBe(true);

			// === Step 10: Verify TARGET-LANGUAGE text in DOCX ===
			const docxText = await extractDocxText(fileBuffer);
			expect(
				docxText.length,
				`[${tc.label}] DOCX document.xml should contain non-trivial text content (got ${docxText.length} chars)`
			).toBeGreaterThan(100);

			const { count, matched, missed } = countMarkerMatches(docxText, tc.contentMarkers);
			expect(
				count,
				`[${tc.label}] DOCX should contain at least ${tc.minMarkerMatches}/${tc.contentMarkers.length} ` +
					`target-language (${tc.targetLang}) content markers. ` +
					`Matched: [${matched.join(', ')}]. Missed: [${missed.join(', ')}]. ` +
					`Text sample (first 300 chars): "${docxText.slice(0, 300)}"`
			).toBeGreaterThanOrEqual(tc.minMarkerMatches);

			// Log successful marker details for diagnostics
			console.log(
				`[${tc.label}] Translation markers: ${count}/${tc.contentMarkers.length} matched. ` +
					`Found: [${matched.join(', ')}]. Missing: [${missed.join(', ')}].`
			);

			// === Step 11: Final screenshot ===
			await page.screenshot({
				path: `${screenshotPrefix}-06-verified.png`,
				fullPage: true
			});
		});
	}
});

/**
 * Translation helper diagnostics — no browser needed.
 *
 * These validate countMarkerMatches and extractDocxText behavior
 * with TRANSLATION-specific data (zh-TW / en-US markers).
 * Mirrors conversion-e2e.spec.ts "Helper diagnostics" pattern.
 */
test.describe('Translation helper diagnostics', () => {
	// --- countMarkerMatches with zh-TW markers ---

	test('countMarkerMatches: zh-TW markers match Chinese text', () => {
		const chineseText = '本合約由甲方與乙方簽訂，涵蓋人工智慧相關服務條款。';
		const zhMarkers = TRANSLATION_TESTS[0].contentMarkers;

		const result = countMarkerMatches(chineseText, zhMarkers);

		// Should match: 甲方, 乙方, 人工智慧, 服務, 條款
		expect(result.count).toBeGreaterThanOrEqual(5);
		expect(result.matched).toContain('甲方');
		expect(result.matched).toContain('乙方');
		expect(result.matched).toContain('人工智慧');
		expect(result.matched).toContain('服務');
		expect(result.matched).toContain('條款');
	});

	test('countMarkerMatches: en-US markers match English text', () => {
		const englishText =
			'This contract agreement between party A and party B covers ' +
			'artificial intelligence service obligations under article 5.';
		const enMarkers = TRANSLATION_TESTS[1].contentMarkers;

		const result = countMarkerMatches(englishText, enMarkers);

		// Should match: contract, agreement, party, service, article,
		//               artificial intelligence, obligation
		expect(result.count).toBeGreaterThanOrEqual(6);
		expect(result.matched).toContain('contract');
		expect(result.matched).toContain('agreement');
		expect(result.matched).toContain('party');
		expect(result.matched).toContain('service');
		expect(result.matched).toContain('article');
		expect(result.matched).toContain('artificial intelligence');
	});

	// --- False positive resistance ---

	test('countMarkerMatches: zh-TW markers do NOT match English text', () => {
		const englishText =
			'This is a standard English contract between Party A and Party B ' +
			'regarding artificial intelligence services and obligations.';
		const zhMarkers = TRANSLATION_TESTS[0].contentMarkers;

		const result = countMarkerMatches(englishText, zhMarkers);

		// Chinese characters should not appear in English text
		expect(result.count).toBe(0);
		expect(result.matched).toEqual([]);
		expect(result.missed).toEqual([...zhMarkers]);
	});

	test('countMarkerMatches: en-US markers do NOT match Chinese text', () => {
		const chineseText = '本合約由甲方與乙方簽訂，涵蓋人工智慧相關服務條款及義務。';
		const enMarkers = TRANSLATION_TESTS[1].contentMarkers;

		const result = countMarkerMatches(chineseText, enMarkers);

		// English words should not appear in Chinese text
		expect(result.count).toBe(0);
		expect(result.matched).toEqual([]);
		expect(result.missed).toEqual([...enMarkers]);
	});

	// --- countMarkerMatches edge cases ---

	test('countMarkerMatches: empty text returns all markers as missed', () => {
		const zhMarkers = TRANSLATION_TESTS[0].contentMarkers;
		const result = countMarkerMatches('', zhMarkers);

		expect(result.count).toBe(0);
		expect(result.matched).toEqual([]);
		expect(result.missed).toEqual([...zhMarkers]);
	});

	test('countMarkerMatches: empty markers returns zero count', () => {
		const result = countMarkerMatches('任意中文內容', []);

		expect(result.count).toBe(0);
		expect(result.matched).toEqual([]);
		expect(result.missed).toEqual([]);
	});

	test('countMarkerMatches: case-insensitive matching for English markers', () => {
		const text = 'CONTRACT and AGREEMENT between PARTY members';
		const markers = ['contract', 'agreement', 'party'];

		const result = countMarkerMatches(text, markers);

		expect(result.count).toBe(3);
		expect(result.missed).toEqual([]);
	});

	// --- extractDocxText edge cases ---

	test('extractDocxText: returns empty string for zip without word/document.xml', async () => {
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		zip.file('dummy.txt', 'not a docx');
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toBe('');
	});

	test('extractDocxText: extracts Chinese text from minimal DOCX-like zip', async () => {
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		const minimalXml = `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>本合約由甲方與乙方簽訂</w:t></w:r></w:p></w:body>
</w:document>`;
		zip.file('word/document.xml', minimalXml);
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toContain('甲方');
		expect(text).toContain('乙方');
		expect(text).toContain('合約');
	});

	test('extractDocxText: extracts English text from minimal DOCX-like zip', async () => {
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		const minimalXml = `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>This contract agreement covers artificial intelligence services</w:t></w:r></w:p></w:body>
</w:document>`;
		zip.file('word/document.xml', minimalXml);
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toContain('contract');
		expect(text).toContain('artificial intelligence');
	});

	test('extractDocxText: handles multi-paragraph XML structure', async () => {
		const JSZipModule = await import('jszip');
		const zip = new JSZipModule.default();
		const multiParaXml = `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一條</w:t></w:r></w:p>
    <w:p><w:r><w:t>甲方義務</w:t></w:r></w:p>
    <w:p><w:r><w:t>第二條</w:t></w:r></w:p>
    <w:p><w:r><w:t>乙方服務</w:t></w:r></w:p>
  </w:body>
</w:document>`;
		zip.file('word/document.xml', multiParaXml);
		const buffer = await zip.generateAsync({ type: 'nodebuffer' });

		const text = await extractDocxText(buffer);
		expect(text).toContain('甲方');
		expect(text).toContain('乙方');
		expect(text).toContain('服務');
		// Verify multi-paragraph text is concatenated
		expect(text.length).toBeGreaterThan(10);
	});
});

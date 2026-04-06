import { test, expect } from "@playwright/test";
import { login } from "../helpers/auth";
import {
  createNewChat,
  selectModel,
  uploadFile,
  triggerSkillAndWaitForStart,
  waitForSkillCompletion,
  CHAT_SELECTORS,
} from "../helpers/chat";

/**
 * E2E test: Verify that only the final DOCX deliverable is returned from
 * the anything-to-docx skill — no intermediate files (.xml, .json, .png).
 *
 * This validates the three-tier output collection strategy in opencode.py:
 *   Tier 2 catches `final-output.docx` and ignores all workspace intermediates.
 *
 * The test runs the full VLM pipeline end-to-end (PDF -> OCR -> layout -> DOCX),
 * which takes several minutes through OpenRouter.
 *
 * Parameterized over two PDFs:
 *   - contract_en3.pdf (93KB, English) — baseline Latin script
 *   - contract_zh3.pdf (226KB, Chinese) — CJK content, larger file
 */

const TEST_MODEL = "openrouter.qwen/qwen3.5-122b-a10b" as const;
const SKILL_TRIGGER_MESSAGE =
  "convert this file to docx using anything-to-docx" as const;

/** Extensions that indicate intermediate/temp files — must NOT appear in output */
const INTERMEDIATE_EXTENSIONS = [".xml", ".json", ".png", ".jpg", ".jpeg", ".bmp", ".txt", ".md"] as const;

/** PK zip magic bytes — DOCX is a zip archive (OOXML) */
const PK_ZIP_MAGIC = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);

/** Test cases — plain data, not classes */
const TEST_PDFS = [
  {
    label: "EN",
    path: "/Users/noelbao/Works/glm-ocr-latest-test/contract_en3.pdf",
    description: "English contract (93KB)",
  },
  {
    label: "ZH",
    path: "/Users/noelbao/Works/glm-ocr-latest-test/contract_zh3.pdf",
    description: "Chinese contract (226KB)",
  },
] as const;

test.describe("Skill Output Filtering", () => {
  // 15-minute timeout per test for the full VLM pipeline
  test.setTimeout(900_000);

  for (const pdf of TEST_PDFS) {
    test(`anything-to-docx [${pdf.label}] returns ONLY the final DOCX, no intermediates`, async ({
      page,
    }) => {
      const screenshotPrefix = `screenshots/skill-output-${pdf.label.toLowerCase()}`;

      // === Step 1: Login ===
      await login(page);
      await page.screenshot({
        path: `${screenshotPrefix}-01-logged-in.png`,
        fullPage: true,
      });

      // === Step 2: Start a fresh chat and select model ===
      await createNewChat(page);
      await selectModel(page, TEST_MODEL);

      // === Step 3: Upload the test PDF ===
      await uploadFile(page, pdf.path);
      await page.screenshot({
        path: `${screenshotPrefix}-02-file-uploaded.png`,
        fullPage: true,
      });

      // === Step 4: Trigger the skill and wait for it to start ===
      await triggerSkillAndWaitForStart(page, SKILL_TRIGGER_MESSAGE);
      await page.screenshot({
        path: `${screenshotPrefix}-03-skill-started.png`,
        fullPage: true,
      });

      // === Step 5: Wait for skill to fully complete ===
      // This is the long step — the VLM pipeline runs through OpenRouter
      const responseText = await waitForSkillCompletion(page);
      await page.screenshot({
        path: `${screenshotPrefix}-04-skill-complete.png`,
        fullPage: true,
      });

      // === Step 6: Verify "Output files" section exists ===
      expect(responseText).toContain("Output files");

      // === Step 7: Verify a .docx download link exists ===
      // The backend renders: - [final-output.docx](/api/v1/files/{id}/content)
      // The frontend renders this as an <a> tag with href containing /api/v1/files/
      const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
      const lastAssistant = assistantMessages.last();

      const docxLinks = lastAssistant.locator('a[href*="/api/v1/files/"]').filter({
        hasText: /\.docx$/i,
      });
      const docxLinkCount = await docxLinks.count();
      expect(docxLinkCount).toBeGreaterThanOrEqual(1);

      // === Step 8: Verify NO intermediate file links exist ===
      // Check that no links to intermediate file types appear in the response
      for (const ext of INTERMEDIATE_EXTENSIONS) {
        const intermediateLinks = lastAssistant
          .locator('a[href*="/api/v1/files/"]')
          .filter({ hasText: new RegExp(`\\${ext}$`, "i") });
        const count = await intermediateLinks.count();
        expect(
          count,
          `[${pdf.label}] Expected no ${ext} file links in output, but found ${count}`,
        ).toBe(0);
      }

      // Also check the raw text for any stray intermediate file references.
      // Use .pop() instead of [1] for robustness — handles "Output files"
      // appearing multiple times in the response (e.g. in status messages).
      const outputFilesSection = responseText.split("Output files").pop() ?? "";
      for (const ext of INTERMEDIATE_EXTENSIONS) {
        expect(
          outputFilesSection,
          `[${pdf.label}] "Output files" section should not mention ${ext} files`,
        ).not.toMatch(new RegExp(`\\w+\\${ext}`, "i"));
      }

      // === Step 9: Download the DOCX and verify it's valid ===
      const firstDocxLink = docxLinks.first();
      const href = await firstDocxLink.getAttribute("href");
      expect(href).toBeTruthy();

      // Download the file via the API
      const downloadResponse = await page.request.get(href!);
      expect(downloadResponse.ok()).toBe(true);

      const fileBuffer = await downloadResponse.body();

      // Verify non-zero size
      expect(
        fileBuffer.length,
        `[${pdf.label}] Downloaded DOCX should have non-zero size`,
      ).toBeGreaterThan(0);

      // Verify PK zip header (DOCX is OOXML = zip archive)
      const header = new Uint8Array(fileBuffer.slice(0, 4));
      expect(
        header[0] === PK_ZIP_MAGIC[0] &&
          header[1] === PK_ZIP_MAGIC[1] &&
          header[2] === PK_ZIP_MAGIC[2] &&
          header[3] === PK_ZIP_MAGIC[3],
        `[${pdf.label}] DOCX file should start with PK zip header (50 4B 03 04), got: ${Array.from(header).map((b) => b.toString(16).padStart(2, "0")).join(" ")}`,
      ).toBe(true);

      // === Step 10: Final screenshot ===
      await page.screenshot({
        path: `${screenshotPrefix}-05-verified.png`,
        fullPage: true,
      });
    });
  }
});

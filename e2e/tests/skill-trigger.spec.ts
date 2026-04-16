import { test, expect } from "@playwright/test";
import { login } from "../helpers/auth";
import {
  selectModel,
  uploadFile,
  triggerSkillAndWaitForStart,
  CHAT_SELECTORS,
} from "../helpers/chat";

/**
 * Model for skill trigger tests — OpenRouter cloud API for reliable availability.
 * The middleware keyword intercept is model-independent (happens before LLM call).
 * Parameter extraction uses the model but works with any instruction-following model.
 */
const TEST_MODEL = "openrouter.qwen/qwen3.5-122b-a10b" as const;

/** Test PDF files */
const TEST_PDF_EN_PATH =
  "/Users/noelbao/Works/glm-ocr-latest-test/contract_en3.pdf" as const;
const TEST_PDF_ZH_PATH =
  "/Users/noelbao/Works/glm-ocr-latest-test/contract_zh3.pdf" as const;

/**
 * The skill trigger message.
 * The middleware does case-insensitive substring match: skill.name.lower() in last_user_msg.lower()
 * "anything-to-docx" is the skill name — it must appear in the message text.
 */
const SKILL_TRIGGER_MESSAGE =
  "convert this file to docx using anything-to-docx" as const;

test.describe("Skill Trigger", () => {
  /**
   * Generous timeout: skill triggering involves:
   * 1. File upload + processing
   * 2. Middleware keyword intercept
   * 3. LLM-based parameter extraction (extracting_params)
   * 4. OpenCode initialization (start)
   * We only wait for the start signal, not full completion.
   */
  test.setTimeout(300_000);

  test("can upload an English PDF and trigger the anything-to-docx skill", async ({
    page,
  }) => {
    // Step 1: Log in
    await login(page);

    // Step 2: Select the test model
    await selectModel(page, TEST_MODEL);

    // Step 3: Upload the English PDF
    await uploadFile(page, TEST_PDF_EN_PATH);

    // Step 4: Screenshot showing file attached
    await page.screenshot({
      path: "screenshots/skill-trigger-en-file-attached.png",
      fullPage: true,
    });

    // Step 5: Send the skill trigger message and wait for execution to start
    await triggerSkillAndWaitForStart(page, SKILL_TRIGGER_MESSAGE);

    // Step 6: Assert skill status indicator is present
    const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
    const statusArea = assistantMessages.last().locator(".status-description");
    const statusCount = await statusArea.count();
    expect(statusCount).toBeGreaterThan(0);

    // Step 7: Screenshot showing skill execution started
    await page.screenshot({
      path: "screenshots/skill-trigger-en-started.png",
      fullPage: true,
    });
  });

  test("can upload a Chinese PDF and trigger the anything-to-docx skill", async ({
    page,
  }) => {
    // Step 1: Log in
    await login(page);

    // Step 2: Select the test model
    await selectModel(page, TEST_MODEL);

    // Step 3: Upload the Chinese PDF
    await uploadFile(page, TEST_PDF_ZH_PATH);

    // Step 4: Screenshot showing file attached
    await page.screenshot({
      path: "screenshots/skill-trigger-zh-file-attached.png",
      fullPage: true,
    });

    // Step 5: Send the skill trigger message and wait for execution to start
    await triggerSkillAndWaitForStart(page, SKILL_TRIGGER_MESSAGE);

    // Step 6: Assert skill status indicator is present
    const assistantMessages = page.locator(CHAT_SELECTORS.assistantMessage);
    const statusArea = assistantMessages.last().locator(".status-description");
    const statusCount = await statusArea.count();
    expect(statusCount).toBeGreaterThan(0);

    // Step 7: Screenshot showing skill execution started
    await page.screenshot({
      path: "screenshots/skill-trigger-zh-started.png",
      fullPage: true,
    });
  });
});

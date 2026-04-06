import { type Page } from "@playwright/test";

/**
 * Authentication helpers for Open WebUI E2E tests.
 *
 * Design: pure functions operating on Page data — no classes, no shared mutable state.
 * Each function takes a Page and credentials, performs an action, returns void.
 * Composable: login = fillAndSubmit + waitForHome + dismissVersionDialog
 */

/** Immutable default credentials — override via function args if needed */
export const DEFAULT_CREDENTIALS = {
  email: "test@example.com",
  password: "test123",
} as const;

/** Selectors — single source of truth, derived from existing Cypress tests + live UI inspection */
export const SELECTORS = {
  emailInput: 'input[autocomplete="email"]',
  passwordInput: 'input[type="password"]',
  submitButton: 'button[type="submit"]',
  /** Chat input on the home page — proves login succeeded and home rendered */
  chatInput: "#chat-input",
  versionDialogButton: "Okay, Let's Go!",
} as const;

/**
 * Dismiss the version changelog dialog if it appears.
 * Non-destructive: if the dialog isn't present, this is a no-op.
 */
export async function dismissVersionDialog(page: Page): Promise<void> {
  try {
    const button = page.getByRole("button", {
      name: SELECTORS.versionDialogButton,
    });
    await button.click({ timeout: 3_000 });
  } catch {
    // Dialog not present — expected in most cases
  }
}

/**
 * Log in to Open WebUI.
 *
 * Flow: navigate to /auth -> fill credentials -> submit -> wait for home page -> dismiss dialog
 *
 * @param page - Playwright Page instance
 * @param email - login email (default: test@example.com)
 * @param password - login password (default: test123)
 */
export async function login(
  page: Page,
  email: string = DEFAULT_CREDENTIALS.email,
  password: string = DEFAULT_CREDENTIALS.password,
): Promise<void> {
  // Set locale to en-US for stable test assertions (matches Cypress approach)
  await page.addInitScript(() => {
    localStorage.setItem("locale", "en-US");
  });

  // Navigate to auth page
  await page.goto("/auth");

  // Fill credentials
  await page.locator(SELECTORS.emailInput).fill(email);
  await page.locator(SELECTORS.passwordInput).fill(password);

  // Submit
  await page.locator(SELECTORS.submitButton).click();

  // Wait for home page — #chat-input indicates successful login and home page rendered
  // 60s timeout: server may be under load from prior skill executions (opencode subprocess)
  await page.locator(SELECTORS.chatInput).waitFor({
    state: "visible",
    timeout: 60_000,
  });

  // Dismiss version dialog if present
  await dismissVersionDialog(page);
}

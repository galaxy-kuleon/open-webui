import { test, expect } from "@playwright/test";
import * as fs from "fs";
import { fileURLToPath } from "url";
import { loginAsAdmin } from "../helpers/admin";

/**
 * Skill ZIP Import — 2 smoke tests.
 *
 * Test approach: both tests use API-level upload via page.request.post with
 * multipart form data. Rationale: the Skills page UI flow (navigate to
 * /workspace/skills → click Import button → setInputFiles on hidden input)
 * requires page navigation and is fragile across environments. The API path
 * is simple, deterministic, and exercises the identical backend validation
 * path that the UI ultimately calls. Admin credentials are consumed exclusively
 * through loginAsAdmin / ADMIN_CREDENTIALS from e2e/helpers/admin.ts.
 *
 * Fixtures:
 *   e2e/fixtures/test-skill.zip  — valid ZIP with SKILL.md at root;
 *                                   frontmatter: name="Test Skill", description=<non-empty>
 *   e2e/fixtures/no-skill.zip    — valid ZIP containing only other.txt (no SKILL.md)
 *
 * Slugified skill ID for "Test Skill": "test-skill"
 * (routers/skills.py:_slugify("Test Skill") → "test-skill")
 */

const UPLOAD_URL = "/api/v1/skills/upload-zip";
const SKILLS_LIST_URL = "/api/v1/skills/";
const SKILL_DELETE_URL = (id: string) => `/api/v1/skills/id/${id}/delete`;

// ESM-safe path resolution — the project uses "type": "module" so __dirname is unavailable
const FIXTURE_VALID_ZIP = fileURLToPath(new URL("../fixtures/test-skill.zip", import.meta.url));
const FIXTURE_NO_SKILL_ZIP = fileURLToPath(new URL("../fixtures/no-skill.zip", import.meta.url));

/** The skill ID derived from "Test Skill" by routers/skills.py:_slugify */
const IMPORTED_SKILL_ID = "test-skill";

async function getToken(page: Parameters<typeof loginAsAdmin>[0]): Promise<string> {
  const token = await page.evaluate(() => localStorage.getItem("token"));
  if (!token) throw new Error("No auth token in localStorage — loginAsAdmin must run first");
  return token;
}

test.describe("Skill ZIP Import", () => {
  test.afterEach(async ({ page }) => {
    // Clean up the imported skill so reruns are idempotent.
    // The negative test never creates a skill, so this delete is a safe no-op for it.
    const token = await getToken(page);
    await page.request.delete(SKILL_DELETE_URL(IMPORTED_SKILL_ID), {
      headers: { Authorization: `Bearer ${token}` },
    });
  });

  // ---------------------------------------------------------------------------
  // Happy path: valid ZIP with SKILL.md at root → HTTP 200, skill in list
  // ---------------------------------------------------------------------------
  test("valid ZIP with SKILL.md imports successfully and skill appears in list", async ({
    page,
  }) => {
    await loginAsAdmin(page);
    const token = await getToken(page);

    const response = await page.request.post(UPLOAD_URL, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: "test-skill.zip",
          mimeType: "application/zip",
          buffer: fs.readFileSync(FIXTURE_VALID_ZIP),
        },
      },
    });

    expect(response.status()).toBe(200);

    // Confirm the skill appears in the list after import
    const listResp = await page.request.get(SKILLS_LIST_URL, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(listResp.ok()).toBe(true);

    const body = await listResp.json();
    // Skills list may return array or paginated {items:[...]} shape — handle both
    const items: { id: string }[] = Array.isArray(body) ? body : (body.items ?? body.data ?? []);
    expect(items.find((s) => s.id === IMPORTED_SKILL_ID)).toBeDefined();
  });

  // ---------------------------------------------------------------------------
  // Negative path: ZIP missing SKILL.md → HTTP 400 "No SKILL.md found"
  //
  // Targets routers/skills.py:299-303 — the skill_md_path search (root + one-level-deep)
  // finds nothing and raises:
  //   HTTPException(status_code=400,
  //     detail='No SKILL.md found in zip archive (checked root and one level deep)')
  //
  // Fixture: e2e/fixtures/no-skill.zip — valid ZIP containing only other.txt.
  // ---------------------------------------------------------------------------
  test("ZIP missing SKILL.md is rejected with HTTP 400 and descriptive error detail", async ({
    page,
  }) => {
    await loginAsAdmin(page);
    const token = await getToken(page);

    const response = await page.request.post(UPLOAD_URL, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: "no-skill.zip",
          mimeType: "application/zip",
          buffer: fs.readFileSync(FIXTURE_NO_SKILL_ZIP),
        },
      },
    });

    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.detail).toContain("No SKILL.md found");
  });
});

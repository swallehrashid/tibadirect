import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";
import { signIn } from "./support/signIn";

/**
 * axe in a real browser, so colour contrast, focus order and landmarks are checked as they are drawn.
 * Every page is checked at a desktop and a 360 px phone width (WCAG AA, plus axe's best practices).
 */
const WIDTHS = [1280, 360];
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];

async function violations(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800); // grids and lazy chunks settle
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  return results.violations.map((v) => `[${v.impact}] ${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`);
}

async function check(page: Page, path: string, width: number) {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(path);
  expect(await violations(page), `${path} at ${width}px`).toEqual([]);
}

async function firstPublicSlug(page: Page): Promise<string> {
  const response = await page.request.get("/api/public/cases?page_size=1");
  const body = (await response.json()) as { items: { slug: string }[] };
  const slug = body.items[0]?.slug;
  if (!slug) throw new Error("The demo data has no public case; run the seed first.");
  return slug;
}

test.describe("public pages", () => {
  for (const width of WIDTHS) {
    test(`have no accessibility violations at ${width}px`, async ({ page }) => {
      const slug = await firstPublicSlug(page);
      for (const path of ["/", "/cases", "/how-it-works", `/c/${slug}`, "/login", "/check", "/privacy", "/terms", "/no-such-page"]) await check(page, path, width);
    });
  }
});

async function asRole(browser: Browser, email: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await signIn(page, email);
  return { context, page };
}

test.describe("signed-in pages", () => {
  test("family pages have no violations", async ({ browser }) => {
    const { context, page } = await asRole(browser, "family1@example.test");
    for (const width of WIDTHS) for (const path of ["/app", "/app/new"]) await check(page, path, width);
    await context.close();
  });

  test("hospital pages have no violations", async ({ browser }) => {
    const { context, page } = await asRole(browser, "clerk@kilele-hospital.test");
    for (const width of WIDTHS) for (const path of ["/hospital", "/hospital/settlements"]) await check(page, path, width);
    await context.close();
  });

  test("admin pages have no violations", async ({ browser }) => {
    const { context, page } = await asRole(browser, "admin@tibadirect.test");
    for (const width of WIDTHS) for (const path of ["/admin", "/admin/review", "/admin/audit", "/admin/jobs", "/admin/agent"]) await check(page, path, width);
    await context.close();
  });
});

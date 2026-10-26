import { expect, test, type Browser, type Page } from "@playwright/test";
import { signIn } from "./support/signIn";

/**
 * The page must never scroll sideways on a small phone, in either language (Swahili strings run longer).
 * 360 px is the target width; 320 px is the smallest phone still in use.
 */
const WIDTHS = [360, 320];
const LANGUAGES = ["en", "sw"] as const;

async function overflowOf(page: Page): Promise<string | null> {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
  const { scrollWidth, clientWidth, wide } = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const offenders = [...document.querySelectorAll("body *")]
      .filter((element) => element.getBoundingClientRect().right > width + 1 && !element.closest('[role="region"], [data-testid="data-grid"], ul[class*="overflow-x-auto"], .overflow-x-auto, .ag-root-wrapper'))
      .slice(0, 3)
      .map((element) => `${element.tagName.toLowerCase()}.${String(element.className).slice(0, 40)}`);
    return { scrollWidth: document.documentElement.scrollWidth, clientWidth: width, wide: offenders };
  });
  return scrollWidth > clientWidth ? `scrolls sideways: ${scrollWidth} > ${clientWidth} (${wide.join(", ")})` : null;
}

async function visit(page: Page, paths: string[], width: number, language: string) {
  await page.setViewportSize({ width, height: 800 });
  for (const path of paths) {
    await page.goto(path);
    expect(await overflowOf(page), `${path} at ${width}px in ${language}`).toBeNull();
  }
}

async function withLanguage(browser: Browser, language: string) {
  const context = await browser.newContext();
  await context.addInitScript((code) => window.localStorage.setItem("tiba.lang", code), language);
  return { context, page: await context.newPage() };
}

for (const language of LANGUAGES) {
  test.describe(`in ${language}`, () => {
    test("public pages fit a phone", async ({ browser }) => {
      const { context, page } = await withLanguage(browser, language);
      const response = await page.request.get("/api/public/cases?page_size=1");
      const slug = ((await response.json()) as { items: { slug: string }[] }).items[0]?.slug;
      for (const width of WIDTHS) await visit(page, ["/", "/cases", "/how-it-works", `/c/${slug}`, "/login", "/check", "/privacy", "/terms"], width, language);
      await context.close();
    });

    test("family pages fit a phone", async ({ browser }) => {
      const { context, page } = await withLanguage(browser, language);
      await signIn(page, "family1@example.test");
      for (const width of WIDTHS) await visit(page, ["/app", "/app/new"], width, language);
      await context.close();
    });

    test("hospital pages fit a phone", async ({ browser }) => {
      const { context, page } = await withLanguage(browser, language);
      await signIn(page, "clerk@kilele-hospital.test");
      for (const width of WIDTHS) await visit(page, ["/hospital", "/hospital/settlements"], width, language);
      await context.close();
    });

    test("admin pages fit a phone", async ({ browser }) => {
      const { context, page } = await withLanguage(browser, language);
      await signIn(page, "admin@tibadirect.test");
      for (const width of WIDTHS) await visit(page, ["/admin", "/admin/review", "/admin/audit", "/admin/jobs"], width, language);
      await context.close();
    });
  });
}

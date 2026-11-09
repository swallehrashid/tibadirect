import { devices, expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { Api, hospitalDecides, kileleBill, publicSlug, startCase, uniqueReference, waitForStatus } from "./support/stack";

/**
 * The demo on the phone it will be filmed on: a mid-range Android in portrait. Every page keeps its demo notice,
 * nothing scrolls sideways, and the things a donor needs (the trust badge, the amount box, the donate button, the
 * public payments) are on the screen and big enough to tap. Set E2E_SCREENSHOTS_DIR to also save a picture of each.
 */
// The device preset names a browser; the stack's own Chrome setting decides that, so only the screen and touch settings are used.
const { defaultBrowserType, ...phone } = devices["Pixel 7"];
void defaultBrowserType;
test.use(phone);
const SHOTS = process.env.E2E_SCREENSHOTS_DIR;

async function look(page: Page, name: string) {
  const width = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, window: window.innerWidth }));
  expect(width.page, `${name} scrolls sideways`).toBeLessThanOrEqual(width.window);
  await expect(page.getByRole("complementary", { name: /demo/i }).or(page.getByText(/this uses PayPal sandbox money/i)).first()).toBeVisible();
  if (SHOTS) {
    mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
  }
}

test("the public pages on a phone", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await look(page, "phone-01-landing");

  await page.goto("/cases");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await look(page, "phone-02-cases");

  await page.goto("/check");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await look(page, "phone-03-check");

  await page.goto("/privacy");
  await expect(page.getByRole("heading", { level: 1, name: "Privacy notice" })).toBeVisible();
  await look(page, "phone-04-privacy");
});

test("the seeded live case on a phone: trust, a donate button to tap, and the public payments", async ({ page }) => {
  await page.goto("/c/demolive2345");
  await expect(page.getByRole("heading", { level: 1, name: "Baraka" })).toBeVisible();
  await expect(page.getByText("Hospital-confirmed")).toBeVisible();
  const amount = page.getByLabel("Amount in US dollars");
  await expect(amount).toBeVisible();
  const donate = page.getByRole("button", { name: /Donate \$\d+(\.\d+)? with PayPal/ });
  await expect(donate).toBeVisible();
  const box = (await donate.boundingBox())!;
  expect(box.height, "the donate button is big enough to tap").toBeGreaterThanOrEqual(44);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await expect(page.getByText("Payment 1:")).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify ledger" })).toBeVisible();
  await look(page, "phone-05-live-case");
});

test("a paused case on a phone says so and offers no way to give", async ({ page }) => {
  const family = await Api.signedIn("family1@example.test");
  const bill = kileleBill({ total: 60_000 });
  const started = await startCase(family, { alias: `Phone ${uniqueReference().slice(-4)}`, bill });
  await waitForStatus(family, started.id, ["pending_confirmation"]);
  await hospitalDecides(await Api.signedIn("clerk@kilele-hospital.test"), started.id, { decision: "confirmed", amount: bill.total, invoice: bill.invoice });
  const slug = await publicSlug(family, started.id);
  const admin = await Api.signedIn("admin@tibadirect.test");
  await admin.expectOk(admin.post(`/api/admin/cases/${started.id}/freeze`, { reason: "The hospital says it never issued this bill." }));

  await page.goto(`/c/${slug}`);
  await expect(page.getByTestId("case-notice").getByText("Paused for review")).toBeVisible();
  await expect(page.getByRole("button", { name: /Donate/ })).toHaveCount(0);
  await look(page, "phone-06-paused-case");
});

import { expect, test } from "@playwright/test";
import { Api, donate, hospitalDecides, kileleBill, publicCase, publicSlug, receiptStatus, startCase, uniqueReference, waitForStatus, waitUntil } from "../support/stack";

/**
 * An administrator freezes a live case. The page stops taking money at once, says it is paused, and every donor is
 * refunded in full, whatever they chose when they gave.
 */
test("freezing a case pauses its page and refunds every donor", async ({ browser }) => {
  test.setTimeout(3 * 60_000);
  const alias = `Pendo ${uniqueReference().slice(-4)}`;
  const family = await Api.signedIn("family2@example.test");
  const bill = kileleBill({ total: 60_000 });
  const started = await startCase(family, { alias, bill });
  await waitForStatus(family, started.id, ["pending_confirmation"]);
  const clerk = await Api.signedIn("clerk@kilele-hospital.test");
  await hospitalDecides(clerk, started.id, { decision: "confirmed", amount: bill.total, invoice: bill.invoice });
  const slug = await publicSlug(family, started.id);

  // Small gifts, so nothing has been sent to the hospital yet; each donor chose something other than a refund.
  const pool = await donate(slug, 40, { choice: "solidarity_pool" });
  const hold = await donate(slug, 30, { choice: "follow_up_care", name: "Njeri" });
  expect((await receiptStatus(pool.receiptToken)).status).toBe("captured");

  const admin = await (await Api.signedIn("admin@tibadirect.test")).page(browser);
  await admin.goto("/admin");
  await admin.getByLabel(/Search by alias/).fill(alias);
  await admin.getByRole("button", { name: "Search" }).click();
  await admin.getByRole("link", { name: new RegExp(`Open\\s*:\\s*${alias}`) }).first().click();
  await expect(admin.getByRole("heading", { level: 1, name: alias })).toBeVisible();
  await admin.getByRole("button", { name: "Freeze case" }).click();
  const dialog = admin.getByRole("alertdialog");
  await expect(dialog.getByText(/every donor is refunded in full, whatever they chose/)).toBeVisible();
  await dialog.getByLabel("Why? (required)").fill("The hospital says it never issued this bill.");
  await dialog.getByRole("button", { name: "Freeze and refund" }).click();
  await expect(admin.getByText("The case is frozen and refunds have started.")).toBeVisible();

  // The public page now says what happened, shows no story, and takes no money.
  const visitor = await browser.newPage();
  await visitor.goto(`/c/${slug}`);
  await expect(visitor.getByRole("heading", { level: 1, name: alias })).toBeVisible();
  await expect(visitor.getByTestId("case-notice").getByText("Paused for review")).toBeVisible();
  await expect(visitor.getByRole("button", { name: /Donate/ })).toHaveCount(0);
  expect((await publicCase(slug)).notice).toBe("paused_for_review");
  const guest = await Api.visitor();
  expect((await guest.post("/api/donations/orders", { case_slug: slug, amount_usd: "20" })).status()).toBe(404);

  // Every donor is refunded, including the ones who had chosen the pool or a hold for follow-up care.
  for (const gift of [pool, hold]) {
    await waitUntil(`the refund of ${gift.receiptToken.slice(0, 6)}`, async () => (await receiptStatus(gift.receiptToken)).status === "refunded", { timeout: 90_000, every: 2_000 });
    await visitor.goto(`/receipt/${gift.receiptToken}`);
    await expect(visitor.getByText("This donation has been refunded in full.")).toBeVisible();
  }
  // The freeze ends with the case cancelled, and the page still says "paused", never "ended".
  await waitUntil("the freeze to finish", async () => (await publicCase(slug)).status === "cancelled", { timeout: 90_000, every: 2_000 });
  expect((await publicCase(slug)).notice).toBe("paused_for_review");
});

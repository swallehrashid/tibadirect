import { expect, test } from "@playwright/test";
import { Api, donate, kileleBill, publicSlug, startCase, uniqueReference, waitForStatus } from "../support/stack";
import { confirmBillInBrowser, donateInBrowser } from "../support/ui";

/**
 * The core promise in one run: a family's bill is checked, the hospital confirms it, donors give through the
 * (simulated) PayPal button, and the hospital is paid, with every step on the public ledger.
 */
test("a confirmed bill, two donors and a payment to the hospital", async ({ browser }) => {
  test.setTimeout(4 * 60_000);
  const alias = `Neema ${uniqueReference().slice(-4)}`;
  const family = await Api.signedIn("family1@example.test");
  const bill = kileleBill({ total: 60_000 });
  const started = await startCase(family, { alias, bill });
  await waitForStatus(family, started.id, ["pending_confirmation"]);

  const clerk = await (await Api.signedIn("clerk@kilele-hospital.test")).page(browser);
  await confirmBillInBrowser(clerk, alias, bill);
  await waitForStatus(family, started.id, ["live"]);
  const slug = await publicSlug(family, started.id);

  const watcher = await browser.newPage();
  await watcher.goto(`/c/${slug}`);
  await expect(watcher.getByRole("heading", { level: 1, name: alias })).toBeVisible();
  await expect(watcher.getByText("Hospital-confirmed")).toBeVisible();

  await donateInBrowser(browser, slug, 200, /Refund me/, "Wanjiru");
  await donate(slug, 120, { choice: "solidarity_pool" });

  // The first payment follows within a minute (a payout is checked every few seconds at first).
  await expect(async () => {
    await watcher.goto(`/c/${slug}`);
    await expect(watcher.getByText("Payment 1:")).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 90_000, intervals: [4000] });
  await watcher.getByRole("button", { name: "Verify ledger" }).click();
  await expect(watcher.getByText("The ledger is intact")).toBeVisible();
  await expect(watcher.getByTestId("ledger-grid").getByText("Donation received from Wanjiru")).toBeVisible();

  await clerk.goto("/hospital/settlements");
  await expect(clerk.getByTestId("settlements-grid").getByText(alias).first()).toBeVisible();
});

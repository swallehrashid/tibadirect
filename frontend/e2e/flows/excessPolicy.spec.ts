import { expect, test } from "@playwright/test";
import { Api, donate, hospitalDecides, kileleBill, publicCase, publicSlug, receiptStatus, startCase, uniqueReference, waitForStatus, waitUntil } from "../support/stack";

/**
 * More is given than the final bill needs. When the case closes, the leftover goes where each donor said at checkout:
 * the last donor chose a refund and is paid back, the one before chose the solidarity pool and the money moves there.
 * Payments to the hospital use the oldest gifts first.
 */
test("money the final bill did not need is returned or pooled as each donor chose", async ({ browser }) => {
  test.setTimeout(5 * 60_000);
  const alias = `Zawadi ${uniqueReference().slice(-4)}`;
  const family = await Api.signedIn("family3@example.test");
  const estimate = kileleBill({ total: 60_000, kind: "ESTIMATE" });
  const started = await startCase(family, { alias, bill: estimate, kind: "estimate" });
  await waitForStatus(family, started.id, ["pending_confirmation"]);
  const clerk = await Api.signedIn("clerk@kilele-hospital.test");
  await hospitalDecides(clerk, started.id, { decision: "confirmed", amount: estimate.total, invoice: estimate.invoice });
  const slug = await publicSlug(family, started.id);

  await donate(slug, 250, { choice: "follow_up_care" });
  await donate(slug, 120, { choice: "refund" });
  const pooled = await donate(slug, 60, { choice: "solidarity_pool" });
  const refunded = await donate(slug, 60, { choice: "refund" });

  // Until the final bill is confirmed no more than 80% of the estimate is paid.
  await waitUntil("the first payment", async () => Number((await publicCase(slug)).settled_kes) > 0, { timeout: 90_000, every: 2_000 });

  // Treatment ends with a bill lower than the estimate (still above what has been paid).
  const final = kileleBill({ total: 51_000 });
  await family.expectOk(family.upload(`/api/cases/${started.id}/final-bill`, "final.pdf", final.pdf));
  await waitUntil("the final bill to reach the hospital", async () => {
    const queue = await clerk.json<{ pending: { alias: string }[] }>("/api/hospital/queue");
    return queue.pending.some((item) => item.alias === alias);
  }, { timeout: 60_000, every: 2_000 });
  await hospitalDecides(clerk, started.id, { decision: "confirmed", amount: final.total, invoice: final.invoice, final: true });

  await waitUntil("the case to close", async () => (await publicCase(slug)).status === "closed", { timeout: 120_000, every: 2_000 });
  await waitUntil("the leftover refund", async () => (await receiptStatus(refunded.receiptToken)).status !== "captured", { timeout: 120_000, every: 2_000 });

  const visitor = await browser.newPage();
  await visitor.goto(`/receipt/${refunded.receiptToken}`);
  await expect(visitor.getByText(/has been refunded in full|of this donation has been refunded/)).toBeVisible();

  // The public ledger shows both movements, and the chain still checks out.
  await visitor.goto(`/c/${slug}`);
  await expect(visitor.getByRole("heading", { name: "This case is closed" })).toBeVisible();
  const ledger = visitor.getByTestId("ledger-grid");
  await expect(ledger.getByText(/Refund/i).first()).toBeVisible();
  await expect(ledger.getByText(/pool/i).first()).toBeVisible();
  await visitor.getByRole("button", { name: "Verify ledger" }).click();
  await expect(visitor.getByText("The ledger is intact")).toBeVisible();
  expect((await receiptStatus(pooled.receiptToken)).status).not.toBe("refunded");
});

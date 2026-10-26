import { expect, test } from "@playwright/test";
import { Api, kileleBill, startCase, uniqueReference, waitForStatus } from "../support/stack";

/** The same bill sent for a second case is held for a person, and the reason says why. */
test("the same bill used for a second case goes to manual review", async ({ browser }) => {
  const bill = kileleBill({ total: 50_000 });
  const first = await Api.signedIn("family3@example.test");
  const firstCase = await startCase(first, { alias: `Amani ${uniqueReference().slice(-4)}`, bill });
  expect(await waitForStatus(first, firstCase.id, ["pending_confirmation", "in_review"])).toBe("pending_confirmation");

  const second = await Api.signedIn("family4@example.test");
  const alias = `Amani ${uniqueReference().slice(-4)}`;
  const secondCase = await startCase(second, { alias, bill });
  expect(await waitForStatus(second, secondCase.id, ["in_review", "pending_confirmation"])).toBe("in_review");

  const triage = await second.json<{ route: string; risk_band: string; reasons_en: string[] }>(`/api/cases/${secondCase.id}/triage`);
  expect(triage.route).toBe("manual_review");
  expect(triage.risk_band).toBe("high");
  expect(triage.reasons_en.join(" ")).toMatch(/exact file has already been used/);

  // The person reviewing sees the case in the queue and the checks that held it.
  const admin = await (await Api.signedIn("admin@tibadirect.test")).page(browser);
  await admin.goto("/admin/review");
  await expect(admin.getByRole("button", { name: new RegExp(`Resolve\\s*:\\s*${alias}`) }).first()).toBeVisible();
  await admin.goto("/admin");
  await admin.getByLabel(/Search by alias/).fill(alias);
  await admin.getByRole("button", { name: "Search" }).click();
  await admin.getByRole("link", { name: new RegExp(`Open\\s*:\\s*${alias}`) }).first().click();
  await expect(admin.getByRole("heading", { level: 1, name: alias })).toBeVisible();
  await expect(admin.getByText(/exact file|same file|duplicate/i).first()).toBeVisible();
});

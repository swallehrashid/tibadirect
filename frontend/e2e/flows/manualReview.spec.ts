import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Api, caseStatus, startCase, uniqueReference, waitForStatus } from "../support/stack";

const HERE = dirname(fileURLToPath(import.meta.url));
// A genuine-looking Kilele Mission Hospital bill whose total was covered with a white box and retyped (data/synthetic, INV-020).
const TAMPERED = readFileSync(join(HERE, "../../../data/synthetic/invoices/INV-020.pdf"));

/**
 * A bill that looks altered never reaches the hospital on the automatic checks alone: a person on the team looks at
 * it first. (On a database where this file was already used, the "same file as another case" check sends it to
 * the same place, so the scenario holds either way.)
 */
test("a bill that looks altered waits for a person, who sends it on to the hospital", async ({ browser }) => {
  const alias = `Baraka ${uniqueReference().slice(-4)}`;
  const family = await Api.signedIn("family2@example.test");
  const started = await startCase(family, { alias, bill: { pdf: TAMPERED, invoice: "INV-020", total: 456_000 } });
  expect(await waitForStatus(family, started.id, ["in_review", "pending_confirmation"])).toBe("in_review");

  const familyPage = await family.page(browser);
  await familyPage.goto(`/app/cases/${started.id}`);
  await expect(familyPage.getByText("A person on our team is looking at your case.")).toBeVisible();

  const admin = await (await Api.signedIn("admin@tibadirect.test")).page(browser);
  await admin.goto("/admin/review");
  const resolve = admin.getByRole("button", { name: new RegExp(`Resolve\\s*:\\s*${alias}`) }).first();
  await expect(resolve).toBeVisible();
  await resolve.click();
  const dialog = admin.getByRole("dialog");
  await dialog.getByLabel("Why? (required)").fill("A person looked at the signals and the original; the hospital can check it.");
  await dialog.getByRole("button", { name: "Record decision" }).click();
  await expect(admin.getByText("Decision recorded").first()).toBeVisible();

  expect(await caseStatus(family, started.id)).toBe("pending_confirmation");
  await familyPage.reload();
  await expect(familyPage.getByText("Waiting for the hospital to confirm your bill.")).toBeVisible();
});

import { expect, test } from "@playwright/test";
import { Api, startCase, uniqueReference, waitForStatus } from "../support/stack";
import { openHospitalReview, sendHospitalCode, typeOtp } from "../support/ui";

/** The billing office does not recognise the amount: it asks the family a question instead of confirming. */
test("the hospital asks the family a question and the case waits for a person", async ({ browser }) => {
  const alias = `Tumaini ${uniqueReference().slice(-4)}`;
  const family = await Api.signedIn("family1@example.test");
  const started = await startCase(family, { alias });
  await waitForStatus(family, started.id, ["pending_confirmation"]);

  const clerk = await (await Api.signedIn("clerk@kilele-hospital.test")).page(browser);
  await openHospitalReview(clerk, alias);
  const code = await sendHospitalCode(clerk);
  await clerk.getByRole("radio", { name: /Ask the family a question/ }).click();
  await clerk.getByLabel("Your question for the family").fill("The total on this bill does not match our records. Please send the stamped copy.");
  await typeOtp(clerk, code);
  await clerk.getByRole("button", { name: "Send the question" }).click();
  await expect(clerk.getByText(/Your question has gone to the family/)).toBeVisible();

  expect(await waitForStatus(family, started.id, ["in_review"])).toBe("in_review");
  const detail = await family.json<{ confirmations: { status: string; query_note: string | null }[] }>(`/api/cases/${started.id}`);
  expect(detail.confirmations.map((c) => c.status)).toContain("queried");
  expect(detail.confirmations.find((c) => c.status === "queried")?.query_note).toMatch(/stamped copy/);

  // The family sees the question, and the case is not public while the bill is in doubt.
  const familyPage = await family.page(browser);
  await familyPage.goto(`/app/cases/${started.id}`);
  await expect(familyPage.getByText(/stamped copy/)).toBeVisible();
  const slug = (await family.json<{ public_slug: string }>(`/api/cases/${started.id}`)).public_slug;
  const visitor = await Api.visitor();
  expect((await visitor.get(`/api/public/cases/${slug}`)).status()).toBe(404);

  // Nobody can give to it.
  const order = await visitor.post("/api/donations/orders", { case_slug: slug, amount_usd: "20" });
  expect(order.status()).toBe(404);
});

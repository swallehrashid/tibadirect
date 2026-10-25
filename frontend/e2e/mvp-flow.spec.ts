import { expect, test, type Browser, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { signIn } from "./support/signIn";

/**
 * The whole product, start to finish, in a real browser against the running stack (PayPal in mock mode):
 *
 *   a family sends a bill  ->  the checks run  ->  the hospital confirms it with a code
 *   ->  three donors give  ->  payments go to the hospital in parts  ->  the family uploads the final bill
 *   ->  the hospital confirms it  ->  the case closes  ->  leftover money is refunded.
 *
 * Set E2E_SCREENSHOTS_DIR to also save a picture of each step (the phase review uses them).
 * It uses the demo accounts and makes new rows every time, so run it against a development stack.
 */
const HERE = dirname(fileURLToPath(import.meta.url));
const INVOICE = join(HERE, "../../data/synthetic/invoices/INV-049.pdf"); // a genuine Kilele Mission Hospital bill, KES 66,700
const BILL_TOTAL = "66700";
// The hospital types its own invoice number; a run number keeps repeated runs from clashing with each other.
const INVOICE_NUMBER = `KMH/26/058340-${Date.now().toString(36)}`;
const SHOTS = process.env.E2E_SCREENSHOTS_DIR;

/** A small, valid one-page PDF whose words differ every run, so it is never taken for a copy of another bill. */
function finalBillPdf(): { name: string; mimeType: string; buffer: Buffer } {
  const text = `Final bill ${INVOICE_NUMBER} total KES 66,700`;
  const stream = `BT /F1 12 Tf 72 720 Td (${text}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(body.length);
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return { name: "final-bill.pdf", mimeType: "application/pdf", buffer: Buffer.from(body, "latin1") };
}

async function shot(page: Page, name: string, fullPage = true) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage });
}

async function mailpitCode(to: string): Promise<string> {
  for (let attempt = 0; attempt < 30; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    const list = (await (await fetch("http://localhost:8025/api/v1/messages")).json()) as { messages: { ID: string; To: { Address: string }[] }[] };
    const hit = list.messages.find((message) => message.To.some((address) => address.Address.includes(to)));
    if (hit) {
      const body = (await (await fetch(`http://localhost:8025/api/v1/message/${hit.ID}`)).json()) as { Text: string };
      const code = /\b(\d{6})\b/.exec(body.Text)?.[1];
      if (code) return code;
    }
  }
  throw new Error(`No code arrived for ${to}`);
}

async function typeOtp(page: Page, code: string) {
  await page.getByRole("textbox", { name: /Digit 1 of 6/ }).click();
  await page.keyboard.type(code);
}

async function donate(browser: Browser, slug: string, dollars: string, choice: RegExp, name: string | null, shotName?: string): Promise<string> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(`/c/${slug}`);
  await page.getByLabel("Amount in US dollars").fill(dollars);
  await page.getByRole("radio", { name: choice }).click();
  if (name) {
    await page.getByRole("checkbox", { name: "Give anonymously" }).click();
    await page.getByLabel(/Name shown in the public ledger/).fill(name);
  }
  if (shotName) await shot(page, `${shotName}-form`, false);
  await page.getByRole("button", { name: /Donate \$\d+(\.\d+)? with PayPal \(simulated\)/ }).click();
  await page.waitForURL(/\/receipt\//);
  await expect(page.getByRole("heading", { name: "Thank you" })).toBeVisible();
  if (shotName) await shot(page, `${shotName}-receipt`);
  const token = new URL(page.url()).pathname.split("/").pop()!;
  await context.close();
  return token;
}

test("a family, a hospital and three donors, from the bill to the refund", async ({ browser, page: family }) => {
  test.setTimeout(30 * 60_000);
  const clearMail = () => fetch("http://localhost:8025/api/v1/messages", { method: "DELETE" });

  // --- 1. The family starts a campaign --------------------------------------------------------------------
  await signIn(family, "family4@example.test");
  await family.goto("/app");
  await shot(family, "01-family-home");
  await family.goto("/app/new");
  await family.getByLabel("Search hospitals").fill("kilele");
  await family.getByRole("radio", { name: /Kilele Mission Hospital/ }).click();
  await family.getByLabel("Name for your page").fill("Imani");
  await family.getByLabel("County or town").fill("Nairobi");
  await family.getByLabel("Patient or admission number from the bill").fill("KMH-ADM-8340");
  await shot(family, "02-wizard-hospital");
  await family.getByRole("button", { name: "Next" }).click();

  await expect(family.getByRole("heading", { name: "What you agree to" })).toBeVisible();
  await family.getByRole("checkbox", { name: /Let the hospital confirm the bill/ }).click();
  await family.getByRole("checkbox", { name: /Show this case on a public page/ }).click();
  await shot(family, "03-wizard-consent");
  await family.getByRole("button", { name: "Next" }).click();

  await expect(family.getByRole("heading", { name: "Upload the hospital bill" })).toBeVisible();
  await family.locator('input[type="file"]:not([capture])').setInputFiles(INVOICE);
  await family.getByRole("button", { name: "Upload this bill" }).click();
  await expect(family.getByText("Received")).toBeVisible();
  await shot(family, "04-wizard-bill");
  await family.getByRole("button", { name: "Next" }).click();

  await expect(family.getByRole("heading", { name: "Your public page" })).toBeVisible();
  await family.getByLabel("Your story (optional)").fill("Our family is raising money for a hospital bill. Every shilling goes straight to the hospital.");
  await expect(family.getByText(/Saved. No medical words found/)).toBeVisible();
  await shot(family, "05-wizard-page");
  await family.getByRole("button", { name: "Next" }).click();

  await expect(family.getByRole("heading", { name: "Check and send" })).toBeVisible();
  await shot(family, "06-wizard-review");
  await family.getByRole("button", { name: "Send for checking" }).click();
  await family.waitForURL(/\/app\/cases\//);
  const caseUrl = family.url();

  // --- 2. The checks run in the background. A bill that looks risky (the same file twice, say) goes to a person
  //        first; otherwise it goes straight to the hospital. ------------------------------------------------
  const waiting = family.getByText("Waiting for the hospital to confirm your bill.");
  const inReview = family.getByText("A person on our team is looking at your case.");
  await expect(async () => {
    await family.reload();
    await expect(waiting.or(inReview)).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 90_000, intervals: [3000] });
  if (await inReview.isVisible()) {
    await shot(family, "07a-family-in-review");
    const reviewerContext = await browser.newContext();
    const reviewer = await reviewerContext.newPage();
    await signIn(reviewer, "admin@tibadirect.test");
    await reviewer.goto("/admin/review");
    await expect(reviewer.getByRole("button", { name: /Resolve\s*:\s*Imani/ }).first()).toBeVisible();
    await shot(reviewer, "07b-admin-review-queue");
    // The queue lists the longest-waiting first, so this run's item is the last one.
    await reviewer.getByRole("button", { name: /Resolve\s*:\s*Imani/ }).last().click();
    const dialog = reviewer.getByRole("dialog");
    await dialog.getByLabel("Why? (required)").fill("A person looked at the signals; the bill is fine to send to the hospital.");
    await shot(reviewer, "07c-admin-resolve", false);
    await dialog.getByRole("button", { name: "Record decision" }).click();
    await expect(reviewer.getByText("Decision recorded").first()).toBeVisible();
    await reviewerContext.close();
  }
  await expect(async () => {
    await family.reload();
    await expect(waiting).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 90_000, intervals: [3000] });
  await shot(family, "07-family-waiting-for-hospital");

  // --- 3. The hospital's clerk confirms the bill with a code sent to the register address ----------------------
  const clerkContext = await browser.newContext();
  const clerk = await clerkContext.newPage();
  await signIn(clerk, "clerk@kilele-hospital.test");
  await expect(clerk.getByRole("heading", { name: /Bills waiting for you/ })).toBeVisible();
  await expect(clerk.getByTestId("queue-pending").filter({ hasText: "Imani" })).toBeVisible();
  await shot(clerk, "08-hospital-queue");
  await clerk.getByRole("link", { name: /Review.*Imani|Imani/ }).first().click();
  await expect(clerk.getByRole("heading", { level: 1, name: "Imani" })).toBeVisible();
  await clerk.waitForTimeout(1500);
  await shot(clerk, "09-hospital-review");
  await clearMail();
  await clerk.getByRole("button", { name: /Send a code to/ }).click();
  await expect(clerk.getByText(/A code was sent to/)).toBeVisible();
  const decisionCode = await mailpitCode("billing@kilele");
  await clerk.getByLabel("Total amount to be paid (KES)").fill(BILL_TOTAL);
  await clerk.getByLabel("Invoice number").fill(INVOICE_NUMBER);
  await expect(clerk.getByText("This matches the amount our software read.")).toBeVisible();
  await typeOtp(clerk, decisionCode);
  await shot(clerk, "10-hospital-decision");
  await clerk.getByRole("button", { name: "Confirm the bill" }).click();
  await expect(clerk.getByText(/The bill is confirmed. The family's page is now live/)).toBeVisible();
  await shot(clerk, "11-hospital-confirmed");

  // --- 4. The case is live; the family sees it and shares the page ------------------------------------------
  await family.goto(caseUrl);
  await expect(family.getByText("Live. Share your page so people can give.")).toBeVisible();
  await shot(family, "12-family-live");
  const slug = ((await family.getByText(/\/c\/[a-z0-9]+$/).first().innerText()).match(/\/c\/([a-z0-9]+)$/) ?? [])[1];
  expect(slug, "the dashboard shows the public page address").toBeTruthy();

  // --- 5. Three donors give; payments to the hospital follow as money arrives -------------------------------------
  const donor = await browser.newContext();
  const first = await donor.newPage();
  await first.goto(`/c/${slug}`);
  await expect(first.getByRole("heading", { level: 1, name: "Imani" })).toBeVisible();
  await expect(first.getByText("Hospital-confirmed")).toBeVisible();
  await shot(first, "13-case-page-before-donations");
  await donor.close();

  await donate(browser, slug!, "250", /Refund me/, "Wanjiru", "14-donation-1");
  await donate(browser, slug!, "150", /Solidarity pool/, null);
  // Payments are made oldest-donation first, so the leftover at the end comes from the last donor, who chose a refund.
  const lastReceipt = await donate(browser, slug!, "200", /Refund me/, "Otieno");

  const publicView = await browser.newContext();
  const pub = await publicView.newPage();
  await expect(async () => {
    await pub.goto(`/c/${slug}`);
    await expect(pub.getByText("Payment 1:")).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 420_000, intervals: [5000] }); // a payout is checked every two minutes, so each round of payments takes two to four
  await expect(pub.getByText("This case is fully funded")).toBeVisible();
  await pub.getByRole("button", { name: "Verify ledger" }).click();
  await expect(pub.getByText("The ledger is intact")).toBeVisible();
  await shot(pub, "15-case-page-funded-with-payments");

  await clerk.goto("/hospital/settlements");
  await expect(clerk.getByTestId("settlements-grid").getByText("Imani").first()).toBeVisible();
  await shot(clerk, "16-hospital-payments-received");

  // --- 6. Treatment ends: the family uploads the final bill and the hospital confirms it -----------------------------
  await family.goto(caseUrl);
  await expect(family.getByRole("heading", { name: "Upload the final bill" })).toBeVisible();
  await family.locator('input[type="file"]:not([capture])').setInputFiles(finalBillPdf());
  await shot(family, "17-family-final-bill");
  await family.getByRole("button", { name: "Send final bill" }).click();
  await expect(family.locator("p", { hasText: "Your final bill is being checked and sent to the hospital." })).toBeVisible();

  await expect(async () => {
    await clerk.goto("/hospital");
    await expect(clerk.getByTestId("queue-pending").filter({ hasText: "Imani" })).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 90_000, intervals: [3000] });
  await clerk.getByRole("link", { name: /Review.*Imani|Imani/ }).first().click();
  await expect(clerk.getByRole("heading", { level: 1, name: "Imani" })).toBeVisible();
  await expect(clerk.getByText(/final bill for a case that is already live/)).toBeVisible();
  await clearMail();
  await clerk.getByRole("button", { name: /Send a code to/ }).click();
  await expect(clerk.getByText(/A code was sent to/)).toBeVisible();
  const finalCode = await mailpitCode("billing@kilele");
  await clerk.getByLabel("Total amount to be paid (KES)").fill(BILL_TOTAL);
  await clerk.getByLabel("Invoice number").fill(INVOICE_NUMBER);
  await typeOtp(clerk, finalCode);
  await shot(clerk, "18-hospital-final-bill");
  await clerk.getByRole("button", { name: "Confirm the bill" }).click();
  await expect(clerk.getByText("Decision recorded").first()).toBeVisible();

  // --- 7. The rest is paid, the case closes, and leftover money goes back as each donor chose ---------------------------
  await expect(async () => {
    await pub.goto(`/c/${slug}`);
    await expect(pub.getByRole("heading", { name: "This case is closed" })).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 480_000, intervals: [6000] });
  await expect(pub.getByText("KES 66,700").first()).toBeVisible();
  await shot(pub, "19-case-page-closed");

  await expect(async () => {
    await pub.goto(`/receipt/${lastReceipt}`);
    await expect(pub.getByText(/of this donation has been refunded|has been refunded in full/)).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 480_000, intervals: [6000] });
  await shot(pub, "20-receipt-refunded");

  await family.goto(caseUrl);
  await expect(family.getByText("Closed. Thank you for sharing your story.")).toBeVisible();
  await shot(family, "21-family-closed");

  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await signIn(admin, "admin@tibadirect.test");
  await admin.goto("/admin");
  await admin.getByLabel(/Search by alias/).fill("Imani");
  await admin.getByRole("button", { name: "Search" }).click();
  await admin.getByRole("link", { name: /Open\s*:\s*Imani/ }).first().click();
  await expect(admin.getByRole("heading", { level: 1, name: "Imani" })).toBeVisible();
  await expect(admin.getByRole("heading", { name: "Payments to the hospital" })).toBeVisible();
  await admin.waitForTimeout(1500);
  await shot(admin, "22-admin-case");
  await admin.goto("/admin");
  await admin.getByRole("button", { name: "Check the whole ledger" }).click();
  await expect(admin.getByText("The ledger is intact")).toBeVisible();
  await shot(admin, "23-admin-overview");

  await clerkContext.close();
  await publicView.close();
  await adminContext.close();
});

import { expect, type Browser, type Page } from "@playwright/test";
import { clearMail, mailedCode } from "./stack";

/** Types a six-digit code into the code boxes. */
export async function typeOtp(page: Page, code: string): Promise<void> {
  await page.getByRole("textbox", { name: /Digit 1 of 6/ }).click();
  await page.keyboard.type(code);
}

/** The hospital clerk's screen for one bill: ask for the code, read it from the register mailbox, decide. */
export async function openHospitalReview(clerk: Page, alias: string): Promise<void> {
  await clerk.goto("/hospital");
  await expect(async () => {
    await clerk.reload();
    await expect(clerk.getByTestId("queue-pending").filter({ hasText: alias })).toBeVisible({ timeout: 3000 });
  }).toPass({ timeout: 60_000, intervals: [2000] });
  await clerk.getByRole("link", { name: new RegExp(alias) }).first().click();
  await expect(clerk.getByRole("heading", { level: 1, name: alias })).toBeVisible();
}

export async function sendHospitalCode(clerk: Page): Promise<string> {
  await clearMail();
  const sentAt = Date.now() - 1_000;
  await clerk.getByRole("button", { name: /Send a code to/ }).click();
  await expect(clerk.getByText(/A code was sent to/)).toBeVisible();
  return mailedCode("billing@kilele", sentAt);
}

export async function confirmBillInBrowser(clerk: Page, alias: string, bill: { total: number; invoice: string }): Promise<void> {
  await openHospitalReview(clerk, alias);
  const code = await sendHospitalCode(clerk);
  await clerk.getByLabel("Total amount to be paid (KES)").fill(String(bill.total));
  await clerk.getByLabel("Invoice number").fill(bill.invoice);
  await typeOtp(clerk, code);
  await clerk.getByRole("button", { name: "Confirm the bill" }).click();
  await expect(clerk.getByText(/The bill is confirmed. The family's page is now live/)).toBeVisible();
}

/** A donor on the public page pays with the simulated PayPal button and lands on the receipt. Returns the receipt token. */
export async function donateInBrowser(browser: Browser, slug: string, dollars: number, choice: RegExp, name?: string): Promise<string> {
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    await page.goto(`/c/${slug}`);
    await page.getByLabel("Amount in US dollars").fill(String(dollars));
    await page.getByRole("radio", { name: choice }).click();
    if (name) {
      await page.getByRole("checkbox", { name: "Give anonymously" }).click();
      await page.getByLabel(/Name shown in the public ledger/).fill(name);
    }
    await page.getByRole("button", { name: /Donate \$\d+(\.\d+)? with PayPal \(simulated\)/ }).click();
    await page.waitForURL(/\/receipt\//);
    await expect(page.getByRole("heading", { name: "Thank you" })).toBeVisible();
    return new URL(page.url()).pathname.split("/").pop()!;
  } finally {
    await context.close();
  }
}

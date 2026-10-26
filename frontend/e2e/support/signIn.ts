import type { Page } from "@playwright/test";

const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";

/** Signs in through the real email-code screens, reading the code from Mailpit (the local mail catcher). */
export async function signIn(page: Page, email: string): Promise<void> {
  await fetch(`${MAILPIT}/api/v1/messages`, { method: "DELETE" });
  await page.goto("/login");
  // Selectors that do not depend on the language the page is in.
  await page.locator('input[type="email"]').fill(email);
  await page.locator('form button[type="submit"]').click();
  await page.waitForURL(/\/login\/verify/);
  let code: string | undefined;
  for (let attempt = 0; attempt < 20 && !code; attempt++) {
    await page.waitForTimeout(500);
    const list = (await (await fetch(`${MAILPIT}/api/v1/messages`)).json()) as { messages: { ID: string; To: { Address: string }[] }[] };
    const hit = list.messages.find((message) => message.To.some((to) => to.Address === email));
    if (hit) {
      const body = (await (await fetch(`${MAILPIT}/api/v1/message/${hit.ID}`)).json()) as { Text: string };
      code = /\b(\d{6})\b/.exec(body.Text)?.[1];
    }
  }
  if (!code) throw new Error(`No sign-in code arrived for ${email}`);
  await page.getByRole("textbox").first().click();
  await page.keyboard.type(code);
  await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

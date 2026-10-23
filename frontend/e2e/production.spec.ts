import { expect, test } from "@playwright/test";

/**
 * The built web app as the API serves it (the Docker image, or Render): caching, security headers,
 * deep links, and that nothing the app needs is blocked by its own Content-Security-Policy.
 * Run it against the API's address, for example: E2E_BASE_URL=http://localhost:5000 npx playwright test e2e/production.spec.ts
 * Against the Vite dev server (which sends no policy) it skips itself.
 */
test.beforeEach(async ({ request }) => {
  const response = await request.get("/");
  test.skip(!response.headers()["content-security-policy"], "Not served by the API: no Content-Security-Policy header (the Vite dev server).");
});

test("the app page is revalidated and carries the security headers", async ({ request }) => {
  const response = await request.get("/");
  expect(response.status()).toBe(200);
  const headers = response.headers();
  expect(headers["cache-control"]).toBe("no-cache");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  const policy = headers["content-security-policy"]!;
  expect(policy).toContain("default-src 'self'");
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).toContain("https://*.paypal.com");
  expect(policy).toContain("font-src 'self' data:");
  expect(policy.split("script-src")[1]!.split(";")[0]).not.toContain("'unsafe-inline'");
});

test("hashed files are cached for a year and any app address returns the page", async ({ request }) => {
  const html = await (await request.get("/")).text();
  const script = /\/assets\/index-[\w-]+\.js/.exec(html)?.[0];
  expect(script, "the page names its script").toBeTruthy();
  const asset = await request.get(script!);
  expect(asset.status()).toBe(200);
  expect(asset.headers()["cache-control"]).toBe("public, max-age=31536000, immutable");
  for (const path of ["/cases", "/c/anything", "/app/cases/123", "/admin/audit"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(200);
    expect(await response.text(), path).toContain('<div id="root">');
  }
});

test("an unknown API address is JSON, not the app, and files that must stay private are not served", async ({ request }) => {
  const missing = await request.get("/api/does-not-exist");
  expect(missing.status()).toBe(404);
  expect((await missing.json()).error.code).toBe("not_found");
  for (const path of ["/.env", "/.git/config"]) {
    const response = await request.get(path);
    expect(await response.text(), path).not.toMatch(/SECRET|\[core\]/);
  }
});

test("the public pages, the ledger grid and the fonts load without a single policy violation", async ({ page }) => {
  const blocked: string[] = [];
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener("securitypolicyviolation", (event) => (window as unknown as { __csp: string[] }).__csp.push(`${event.violatedDirective} blocked ${event.blockedURI.slice(0, 80)}`));
  });
  const slug = ((await (await page.request.get("/api/public/cases?page_size=1")).json()) as { items: { slug: string }[] }).items[0]?.slug;
  for (const path of ["/", "/cases", "/how-it-works", `/c/${slug}`, "/check", "/login"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    if (path.startsWith("/c/")) await expect(page.getByTestId("ledger-grid").getByRole("row").first()).toBeVisible();
    blocked.push(...(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp)).map((entry) => `${path}: ${entry}`));
  }
  expect(blocked).toEqual([]);
  expect(await page.evaluate(async () => { await document.fonts.ready; return [...document.fonts].some((font) => font.status === "loaded"); })).toBe(true);
});

test("PayPal's script is allowed to load, and a deep link survives a reload", async ({ page, request }) => {
  const config = (await (await request.get("/api/public/config")).json()) as { paypal_mode: string };
  const slug = ((await (await request.get("/api/public/cases?page_size=1")).json()) as { items: { slug: string }[] }).items[0]?.slug;
  const blocked: string[] = [];
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener("securitypolicyviolation", (event) => (window as unknown as { __csp: string[] }).__csp.push(`${event.violatedDirective} blocked ${event.blockedURI.slice(0, 80)}`));
  });
  await page.goto(`/c/${slug}`);
  await page.waitForLoadState("networkidle");
  if (config.paypal_mode === "sandbox") await page.waitForTimeout(3000); // PayPal's files arrive after the page is idle
  blocked.push(...(await page.evaluate(() => (window as unknown as { __csp: string[] }).__csp)));
  expect(blocked).toEqual([]);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});

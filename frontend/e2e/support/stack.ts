import { execFileSync } from "node:child_process";
import { expect, request as playwrightRequest, type APIRequestContext, type APIResponse, type Browser, type Page } from "@playwright/test";

/**
 * Helpers for the end-to-end flows. The browser is used for what a person does on the screen (the hospital's
 * decision, the administrator's review, the donor's payment); the API is used to set the scene (a family sending a
 * bill) so each scenario takes seconds and does not depend on the others.
 */
export const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";
export const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

/** The sign-in screens allow five codes per address per 15 minutes; the flows sign in far more often than that. */
export function clearRateLimits(): void {
  const script = "redis-cli --scan --pattern 'LIMIT*' | xargs -r redis-cli del";
  const url = process.env.E2E_REDIS_URL;
  try {
    if (url) execFileSync("sh", ["-c", script.replaceAll("redis-cli", `redis-cli -u ${url}`)], { stdio: "ignore" });
    else execFileSync("docker", ["compose", "exec", "-T", "redis", "sh", "-c", script], { stdio: "ignore", cwd: process.env.E2E_COMPOSE_DIR ?? ".." });
  } catch {
    // Without a Redis to reach, the limits stay; a run that signs in rarely still passes.
  }
}

export async function clearMail(): Promise<void> {
  await fetch(`${MAILPIT}/api/v1/messages`, { method: "DELETE" });
}

/** The newest six-digit code mailed to an address containing ``to``. */
export async function mailedCode(to: string, since = 0): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const list = (await (await fetch(`${MAILPIT}/api/v1/messages`)).json()) as { messages: { ID: string; Created: string; To: { Address: string }[] }[] };
    const hit = list.messages.find((message) => Date.parse(message.Created) >= since && message.To.some((address) => address.Address.includes(to)));
    if (hit) {
      const body = (await (await fetch(`${MAILPIT}/api/v1/message/${hit.ID}`)).json()) as { Text: string };
      const code = /\b(\d{6})\b/.exec(body.Text)?.[1];
      if (code) return code;
    }
  }
  throw new Error(`No code arrived for ${to}`);
}

export async function waitUntil<T>(what: string, read: () => Promise<T | undefined | false>, { timeout = 60_000, every = 1_000 } = {}): Promise<T> {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (value) return value;
    if (Date.now() > deadline) throw new Error(`Timed out after ${timeout / 1000} s waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, every));
  }
}

// --------------------------------------------------------------------------------------------- bills

/** A one-page PDF with the given lines of text. */
export function pdfWith(lines: string[]): Buffer {
  const escape = (line: string) => line.replace(/[\\()]/g, (character) => `\\${character}`);
  const stream = `BT /F1 11 Tf 14 TL 50 750 Td ${lines.map((line) => `(${escape(line)}) Tj T*`).join(" ")} ET`;
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
  return Buffer.from(body, "latin1");
}

const PHONE_LINE = "Nairobi  Tel +254 20 555 0101  billing@kilele-hospital.test";

export function uniqueReference(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`.toUpperCase();
}

/** A bill from Kilele Mission Hospital whose lines add up, with an invoice number nobody has used. */
export function kileleBill(options: { total?: number; invoice?: string; kind?: "INVOICE" | "ESTIMATE" } = {}): { pdf: Buffer; invoice: string; total: number } {
  const total = options.total ?? 60_000;
  const invoice = options.invoice ?? `KMH/26/${uniqueReference()}`;
  const theatre = total - 20_000 - 10_000;
  const money = (amount: number) => amount.toLocaleString("en-US");
  const pdf = pdfWith([
    "Kilele Mission Hospital",
    PHONE_LINE,
    `${options.kind ?? "INVOICE"} No ${invoice}`,
    "Date 20/10/2026",
    "Bed charges 5 x 4,000 = 20,000",
    `Theatre 1 x ${money(theatre)} = ${money(theatre)}`,
    "Drugs 1 x 10,000 = 10,000",
    `TOTAL KES ${money(total)}`,
    `BALANCE KES ${money(total)}`,
  ]);
  return { pdf, invoice, total };
}

// ------------------------------------------------------------------------------------------------- API

/** One signed-in person (or an anonymous visitor) talking to the API, sending the CSRF token the way the web app does. */
export class Api {
  private constructor(
    readonly http: APIRequestContext,
    private csrf: string,
  ) {}

  static async visitor(): Promise<Api> {
    const http = await playwrightRequest.newContext({ baseURL: BASE_URL });
    const api = new Api(http, "");
    await api.refreshCsrf();
    return api;
  }

  static async signedIn(email: string): Promise<Api> {
    clearRateLimits();
    const api = await Api.visitor();
    const sentAt = Date.now() - 1_000;
    await api.expectOk(api.post("/api/auth/request-code", { email }));
    const code = await mailedCode(email, sentAt);
    await api.expectOk(api.post("/api/auth/verify", { email, code }));
    await api.refreshCsrf();
    return api;
  }

  private async refreshCsrf(): Promise<void> {
    this.csrf = ((await (await this.http.get("/api/auth/csrf")).json()) as { csrf_token: string }).csrf_token;
  }

  get(path: string): Promise<APIResponse> {
    return this.http.get(path);
  }

  post(path: string, data?: unknown): Promise<APIResponse> {
    return this.http.post(path, { data, headers: { "X-CSRFToken": this.csrf } });
  }

  upload(path: string, name: string, buffer: Buffer, fields: Record<string, string> = {}): Promise<APIResponse> {
    return this.http.post(path, { multipart: { file: { name, mimeType: "application/pdf", buffer }, ...fields }, headers: { "X-CSRFToken": this.csrf } });
  }

  async expectOk<T = Record<string, unknown>>(response: Promise<APIResponse>): Promise<T> {
    const done = await response;
    const text = await done.text();
    expect(done.ok(), `${done.url()} answered ${done.status()}: ${text.slice(0, 300)}`).toBe(true);
    return (text ? JSON.parse(text) : {}) as T;
  }

  async json<T = Record<string, unknown>>(path: string): Promise<T> {
    return this.expectOk<T>(this.get(path));
  }

  /** A browser context signed in as this person, so the screen starts where the person would be after signing in. */
  async page(browser: Browser): Promise<Page> {
    const context = await browser.newContext({ storageState: await this.http.storageState(), baseURL: BASE_URL });
    return context.newPage();
  }

  dispose(): Promise<void> {
    return this.http.dispose();
  }
}

// -------------------------------------------------------------------------------------------- scenes

export interface StartedCase {
  id: string;
  alias: string;
  invoice: string;
  total: number;
  /** When the family's "send for checking" was answered, in milliseconds since 1970. */
  submittedAt: number;
}

/** A family signs in, names a case at Kilele Mission Hospital, agrees, uploads a bill and sends it for checking. */
export async function startCase(family: Api, options: { alias?: string; bill?: { pdf: Buffer; invoice: string; total: number }; kind?: "invoice" | "estimate"; hospital?: string } = {}): Promise<StartedCase> {
  const alias = options.alias ?? "Zuri";
  const bill = options.bill ?? kileleBill({ kind: options.kind === "estimate" ? "ESTIMATE" : "INVOICE" });
  const hospitals = await family.json<{ items: { id: string }[] }>(`/api/hospitals?query=${options.hospital ?? "kilele"}`);
  const made = await family.expectOk<{ id: string }>(family.post("/api/cases", { hospital_id: hospitals.items[0]!.id, alias, region: "Nairobi", patient_reference: `ADM-${uniqueReference()}` }));
  const texts = await family.json<{ items: { scope: string; version: string }[] }>("/api/consents/texts?language=en");
  for (const text of texts.items) {
    if (text.scope === "hospital_verification" || text.scope === "public_page") {
      await family.expectOk(family.post(`/api/cases/${made.id}/consents`, { scope: text.scope, actor: "self", wording_version: text.version, language: "en" }));
    }
  }
  await family.expectOk(family.upload(`/api/cases/${made.id}/documents`, "bill.pdf", bill.pdf, { kind: options.kind ?? "invoice" }));
  await family.expectOk(family.post(`/api/cases/${made.id}/submit`));
  return { id: made.id, alias, invoice: bill.invoice, total: bill.total, submittedAt: Date.now() };
}

export async function caseStatus(family: Api, id: string): Promise<string> {
  return (await family.json<{ status: string }>(`/api/cases/${id}`)).status;
}

export async function waitForStatus(family: Api, id: string, wanted: string[], timeout = 60_000): Promise<string> {
  return waitUntil(`the case to be ${wanted.join(" or ")}`, async () => {
    const status = await caseStatus(family, id);
    return wanted.includes(status) ? status : false;
  }, { timeout });
}

/** The hospital's decision, typed the way the portal does it: ask for a code, read it from the register mailbox, decide. */
export async function hospitalDecides(
  clerk: Api,
  caseId: string,
  decision: { decision: "confirmed" | "queried" | "rejected"; amount?: number; invoice?: string; final?: boolean; note?: string },
  mailbox = "billing@kilele",
): Promise<Record<string, unknown>> {
  const sentAt = Date.now() - 1_000;
  const requested = await clerk.expectOk<{ request_id: string }>(clerk.post(`/api/hospital/cases/${caseId}/otp`, { purpose: "decision" }));
  const otp = await mailedCode(mailbox, sentAt);
  return clerk.expectOk(
    clerk.post(`/api/hospital/cases/${caseId}/decision`, {
      request_id: requested.request_id,
      otp,
      decision: decision.decision,
      amount_kes: decision.amount,
      invoice_number: decision.invoice,
      is_final_bill: decision.final ? true : undefined,
      note: decision.note,
    }),
  );
}

export interface Gift {
  receiptToken: string;
  amount: number;
}

/** A donor pays in the simulated PayPal and the money is captured. */
export async function donate(slug: string, usd: number, options: { choice?: "refund" | "solidarity_pool" | "follow_up_care"; name?: string } = {}): Promise<Gift> {
  const donor = await Api.visitor();
  try {
    const order = await donor.expectOk<{ order_id: string }>(
      donor.post("/api/donations/orders", {
        case_slug: slug,
        amount_usd: String(usd),
        excess_choice: options.choice ?? "refund",
        ...(options.name ? { donor_display_name: options.name, is_anonymous: false } : {}),
      }),
    );
    const captured = await donor.expectOk<{ receipt_token: string }>(donor.post(`/api/donations/orders/${order.order_id}/capture`));
    return { receiptToken: captured.receipt_token, amount: usd };
  } finally {
    await donor.dispose();
  }
}

export interface PublicCaseView {
  status: string;
  notice: string | null;
  pledged_kes: string;
  settled_kes: string;
  slug: string;
}

export async function publicCase(slug: string): Promise<PublicCaseView> {
  const visitor = await Api.visitor();
  try {
    return await visitor.json<PublicCaseView>(`/api/public/cases/${slug}`);
  } finally {
    await visitor.dispose();
  }
}

export async function receiptStatus(token: string): Promise<{ status: string; refunded_usd_minor: number }> {
  const visitor = await Api.visitor();
  try {
    return await visitor.json(`/api/donations/receipts/${token}`);
  } finally {
    await visitor.dispose();
  }
}

/** Opens the case a family started and returns its public address. */
export async function publicSlug(family: Api, id: string): Promise<string> {
  return (await family.json<{ public_slug: string }>(`/api/cases/${id}`)).public_slug;
}

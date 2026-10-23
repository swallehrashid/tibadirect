import { vi } from "vitest";
import { forgetCsrfToken } from "@/api";

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
  csrf: string | null;
}

type Reply = Response | { status?: number; body?: unknown; headers?: Record<string, string> } | unknown;
type Handler = (call: RecordedCall) => Reply;

const DEFAULT_CONFIG = {
  paypal_client_id: "",
  paypal_mode: "mock",
  currency: "USD",
  demo_banner: false,
  features: { agent: false, appeal_checker: true },
};

function toResponse(reply: Reply): Response {
  if (reply instanceof Response) return reply;
  if (reply && typeof reply === "object" && ("status" in reply || "body" in reply) && Object.keys(reply).every((k) => ["status", "body", "headers"].includes(k))) {
    const { status = 200, body, headers } = reply as { status?: number; body?: unknown; headers?: Record<string, string> };
    return new Response(status === 204 ? null : JSON.stringify(body ?? {}), { status, headers: { "Content-Type": "application/json", ...headers } });
  }
  return new Response(JSON.stringify(reply ?? {}), { status: 200, headers: { "Content-Type": "application/json" } });
}

/**
 * Replaces fetch with a table of "METHOD /path" handlers (no query string in the key) and records
 * every call. A handler may return a plain object (a 200 JSON answer), a Response, or
 * {status, body}. Unlisted routes answer 404 so a test notices an unexpected call.
 */
export function mockApi(handlers: Record<string, Handler | Reply> = {}) {
  forgetCsrfToken();
  const calls: RecordedCall[] = [];
  const table: Record<string, Handler | Reply> = {
    "GET /api/auth/csrf": { csrf_token: "test-token" },
    "GET /api/public/config": DEFAULT_CONFIG,
    "GET /api/cases": { items: [], page: 1, page_size: 50, total: 0 },
    ...handlers,
  };
  const fetchMock = vi.fn(async (input: Request | string, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(input, window.location.origin), init);
    const url = new URL(request.url);
    let body: unknown = null;
    if (request.method !== "GET") {
      if ((request.headers.get("content-type") ?? "").includes("multipart/form-data")) {
        // Uploads: record the form fields, and the file by name and type. (The body is read as text and split by
        // hand: the platform's own parser refuses jsdom's File objects.)
        const text = await request.clone().text();
        const fields: Record<string, unknown> = {};
        for (const part of text.matchAll(/Content-Disposition: form-data; name="([^"]+)"(?:; filename="([^"]*)")?\r\n(?:Content-Type: ([^\r\n]*)\r\n)?\r\n([\s\S]*?)\r\n--/gi)) {
          const [, name, filename, type, value] = part;
          fields[name!] = filename === undefined ? value : { file: filename, type, size: value!.length };
        }
        body = fields;
      } else {
        const text = await request.clone().text();
        body = text ? JSON.parse(text) : null;
      }
    }
    const call: RecordedCall = { method: request.method, path: url.pathname, body, csrf: request.headers.get("X-CSRFToken") };
    calls.push(call);
    const entry = table[`${request.method} ${url.pathname}`];
    if (entry === undefined) return new Response(JSON.stringify({ error: { code: "not_found", message: "No such route in the test." } }), { status: 404, headers: { "Content-Type": "application/json" } });
    return toResponse(typeof entry === "function" ? (entry as Handler)(call) : entry);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    called: (method: string, path: string) => calls.filter((call) => call.method === method && call.path === path),
  };
}

export const familyUser = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "family1@example.test",
  display_name: "Demo Family 1",
  role: "family",
  preferred_language: "en",
  hospital: null,
};

export const adminUser = { ...familyUser, id: "22222222-2222-4222-8222-222222222222", email: "admin@tibadirect.test", display_name: "Demo Admin", role: "admin" };

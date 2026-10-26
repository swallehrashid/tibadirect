import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, forgetCsrfToken, isApiError, unwrap } from "@/api";

type Call = { url: string; method: string; csrf: string | null };

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("api client", () => {
  const calls: Call[] = [];
  let tokenCount = 0;
  let handler: (request: Request) => Response;

  beforeEach(() => {
    calls.length = 0;
    tokenCount = 0;
    forgetCsrfToken();
    handler = () => json({});
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(new URL(input, "http://localhost"), init);
        calls.push({
          url: new URL(request.url).pathname,
          method: request.method,
          csrf: request.headers.get("X-CSRFToken"),
        });
        if (new URL(request.url).pathname === "/api/auth/csrf") return json({ csrf_token: `token-${++tokenCount}` });
        return handler(request);
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("does not ask for a token on reads", async () => {
    await api.GET("/api/health");
    expect(calls.map((c) => c.url)).toEqual(["/api/health"]);
    expect(calls[0]?.csrf).toBeNull();
  });

  it("fetches the token once and sends it on every write", async () => {
    await api.POST("/api/auth/logout");
    await api.POST("/api/auth/logout");
    expect(calls.filter((c) => c.url === "/api/auth/csrf")).toHaveLength(1);
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => c.csrf)).toEqual(["token-1", "token-1"]);
  });

  it("refreshes the token and retries once when the API refuses it", async () => {
    let first = true;
    handler = () => {
      if (first) {
        first = false;
        return json({ error: { code: "csrf_failed", message: "Bad token." } }, 403);
      }
      return json({}, 200);
    };
    const result = await api.POST("/api/auth/logout");
    expect(result.response.status).toBe(200);
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => c.csrf)).toEqual(["token-1", "token-2"]);
  });

  it("does not retry other 403 answers", async () => {
    handler = () => json({ error: { code: "forbidden", message: "No." } }, 403);
    const result = await api.POST("/api/auth/logout");
    expect(result.response.status).toBe(403);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
  });

  it("turns an error answer into an ApiError with the code, fields and retry delay", async () => {
    handler = () =>
      json(
        { error: { code: "validation_error", message: "Check the form.", details: { json: { email: ["Not an email."] } }, request_id: "r1" } },
        422,
        { "Retry-After": "7" },
      );
    const result = await api.POST("/api/auth/request-code", { body: { email: "x" } });
    let caught: unknown;
    try {
      unwrap(result);
    } catch (error) {
      caught = error;
    }
    expect(isApiError(caught, "validation_error")).toBe(true);
    if (!isApiError(caught)) throw new Error("expected an ApiError");
    expect(caught.status).toBe(422);
    expect(caught.requestId).toBe("r1");
    expect(caught.retryAfter).toBe(7);
    expect(caught.fieldErrors).toEqual({ email: "Not an email." });
  });
});

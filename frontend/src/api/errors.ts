import type { components } from "./schema";

type ErrorBody = components["schemas"]["ErrorBody"];

/** An error answer from the API, in the shape documented in docs/api/CONVENTIONS.md. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;
  /** Seconds to wait before retrying, from the Retry-After header of a 429. */
  readonly retryAfter: number | null;

  constructor(status: number, body: ErrorBody | undefined, retryAfter: number | null = null) {
    super(body?.message || "Something went wrong. Please try again.");
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "unknown_error";
    this.details = body?.details ?? {};
    this.requestId = body?.request_id ?? null;
    this.retryAfter = retryAfter;
  }

  /** Field-level messages from a 422, keyed by field name. */
  get fieldErrors(): Record<string, string> {
    const raw = (this.details.json ?? this.details.query ?? this.details) as Record<string, unknown>;
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(raw)) {
      if (Array.isArray(value) && typeof value[0] === "string") out[key] = value[0];
      else if (typeof value === "string") out[key] = value;
    }
    return out;
  }
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code);
}

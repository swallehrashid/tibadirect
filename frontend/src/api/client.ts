import createClient from "openapi-fetch";
import { ApiError } from "./errors";
import { csrfToken, forgetCsrfToken, MUTATING } from "./csrf";
import type { components, paths } from "./schema";

async function isCsrfRefusal(response: Response): Promise<boolean> {
  if (response.status !== 403) return false;
  try {
    const body = (await response.clone().json()) as components["schemas"]["ErrorEnvelope"];
    return body.error?.code === "csrf_failed";
  } catch {
    return false;
  }
}

/**
 * Wraps fetch so every write carries X-CSRFToken. A refused token (the session changed in
 * another tab, or expired) is refreshed and the request is sent once more.
 */
export async function apiFetch(request: Request): Promise<Response> {
  if (!MUTATING.has(request.method)) return fetch(request);
  const retry = request.clone();
  request.headers.set("X-CSRFToken", await csrfToken());
  const response = await fetch(request);
  if (!(await isCsrfRefusal(response))) return response;
  forgetCsrfToken();
  retry.headers.set("X-CSRFToken", await csrfToken());
  return fetch(retry);
}

// Paths in the OpenAPI document already start with /api, so the base URL is the page's own origin.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: "include",
  fetch: apiFetch,
});

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Returns the data of a call, or throws an ApiError, which is what TanStack Query expects. */
export function unwrap<T>(result: Result<T>): T {
  if (result.data !== undefined) return result.data;
  const retryAfter = Number(result.response.headers.get("Retry-After"));
  const envelope = result.error as components["schemas"]["ErrorEnvelope"] | undefined;
  throw new ApiError(result.response.status, envelope?.error, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null);
}

/** Like unwrap, for calls that answer 204 with no body. */
export function unwrapEmpty(result: Result<unknown>): void {
  if (result.response.ok) return;
  unwrap({ ...result, data: undefined });
}

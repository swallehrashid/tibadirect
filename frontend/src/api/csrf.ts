/**
 * The CSRF token is tied to the session cookie, so it is fetched once, kept in memory, and
 * dropped whenever the session changes (sign-in, sign-out) or the API says it was refused.
 */
let cached: string | null = null;
let inflight: Promise<string> | null = null;

export const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function forgetCsrfToken(): void {
  cached = null;
  inflight = null;
}

export async function csrfToken(fetcher: typeof fetch = fetch): Promise<string> {
  if (cached) return cached;
  inflight ??= fetcher("/api/auth/csrf", { credentials: "include" })
    .then(async (response) => {
      if (!response.ok) throw new Error("Could not start a secure session.");
      const body = (await response.json()) as { csrf_token?: string };
      if (!body.csrf_token) throw new Error("Could not start a secure session.");
      cached = body.csrf_token;
      return cached;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

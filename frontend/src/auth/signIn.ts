import { isApiError } from "@/api";

export const LOGIN_EMAIL_KEY = "tiba.loginEmail";

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** Turns an API error from a sign-in call into a sentence for the person. */
export function signInErrorMessage(error: unknown, t: Translate): string {
  if (isApiError(error)) {
    if (error.status === 429) return t("error.rateLimited", { seconds: error.retryAfter ?? 60 });
    if (error.status === 401) return t("auth.codeWrong");
    if (error.status === 422) return t("auth.emailInvalid");
  }
  if (error instanceof TypeError) return t("error.offline");
  return t("error.body");
}

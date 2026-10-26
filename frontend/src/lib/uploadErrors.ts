import { isApiError } from "@/api";

/** What to tell someone when a bill upload is refused. */
export function uploadErrorMessage(error: unknown, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (isApiError(error)) {
    if (error.status === 413) return t("upload.errors.size");
    if (error.status === 415) return t("upload.errors.type");
    if (error.status === 429) return t("error.rateLimited", { seconds: error.retryAfter ?? 60 });
    if (error.status === 409 && error.message) return error.message;
  }
  if (error instanceof TypeError) return t("error.offline");
  return t("wizard.bill.uploadFailed");
}

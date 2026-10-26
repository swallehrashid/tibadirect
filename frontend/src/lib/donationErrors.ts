import { isApiError } from "@/api";

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** What to tell a donor when starting or finishing a donation fails. Never shows technical text. */
export function donationErrorMessage(error: unknown, t: Translate): string {
  if (isApiError(error)) {
    switch (error.code) {
      case "case_fully_funded":
        return t("donate.errors.fullyFunded");
      case "case_not_accepting":
        return t("donate.errors.notAccepting");
      case "payment_pending":
        return t("donate.errors.pending");
      case "payment_declined":
      case "payer_action_required":
        return t("donate.errors.declined");
      case "amount_invalid":
      case "amount_out_of_range":
        return t("donate.errors.amount");
      case "paypal_unavailable":
      case "paypal_bad_response":
        return t("donate.errors.paypalDown");
      default:
        break;
    }
    if (error.status === 429) return t("error.rateLimited", { seconds: error.retryAfter ?? 60 });
  }
  if (error instanceof TypeError) return t("error.offline");
  return t("donate.errors.generic");
}

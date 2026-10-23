import { ApiError } from "@/api/errors";
import i18n from "@/i18n";
import { donationErrorMessage } from "./donationErrors";

const t = i18n.t.bind(i18n);
const api = (status: number, code: string, retryAfter: number | null = null) => new ApiError(status, { code, message: "technical text" }, retryAfter);

describe("donationErrorMessage", () => {
  it("reassures the donor that they were not charged when the case is full or the payment fails", () => {
    expect(donationErrorMessage(api(409, "case_fully_funded"), t)).toMatch(/not been charged/);
    expect(donationErrorMessage(api(422, "payment_declined"), t)).toMatch(/not been charged/);
    expect(donationErrorMessage(api(502, "paypal_unavailable"), t)).toMatch(/not been charged/);
    expect(donationErrorMessage(new Error("boom"), t)).toMatch(/not been charged/);
  });

  it("explains a pending payment and a closed case", () => {
    expect(donationErrorMessage(api(409, "payment_pending"), t)).toMatch(/still checking/);
    expect(donationErrorMessage(api(409, "case_not_accepting"), t)).toMatch(/not taking donations/);
  });

  it("says how long to wait when rate limited, and never shows technical text", () => {
    const message = donationErrorMessage(api(429, "rate_limited", 90), t);
    expect(message).toContain("90 seconds");
    expect(donationErrorMessage(api(500, "internal_error"), t)).not.toContain("technical text");
  });

  it("explains a network failure", () => {
    expect(donationErrorMessage(new TypeError("Failed to fetch"), t)).toMatch(/could not reach/i);
  });
});

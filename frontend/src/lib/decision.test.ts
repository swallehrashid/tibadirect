import { compareWithBill, validateDecision, type DecisionValues } from "./decision";

const base: DecisionValues = { decision: "confirmed", amountMinor: 24_500_000, invoice: "KMH/26/1", depositMinor: null, note: "", otp: "123456", requestId: "r1" };

describe("validateDecision", () => {
  it("accepts a complete confirmation", () => {
    expect(validateDecision(base)).toEqual({});
  });

  it("needs an amount and an invoice number to confirm", () => {
    expect(validateDecision({ ...base, amountMinor: null, invoice: " " })).toEqual({ amount: "hospital.decision.errors.amount", invoice: "hospital.decision.errors.invoice" });
    expect(validateDecision({ ...base, amountMinor: 0 }).amount).toBe("hospital.decision.errors.amount");
  });

  it("rejects an amount so large it must be a typing error", () => {
    expect(validateDecision({ ...base, amountMinor: 5_000_000_001 }).amount).toBe("hospital.decision.errors.amountLarge");
    expect(validateDecision({ ...base, amountMinor: 5_000_000_000 }).amount).toBeUndefined();
  });

  it("does not let the deposit exceed the bill", () => {
    expect(validateDecision({ ...base, depositMinor: 25_000_000 }).deposit).toBe("hospital.decision.errors.deposit");
    expect(validateDecision({ ...base, depositMinor: 24_500_000 }).deposit).toBeUndefined();
  });

  it("asks for a note instead of an amount when querying or rejecting", () => {
    expect(validateDecision({ ...base, decision: "queried", amountMinor: null, invoice: "", note: "" })).toEqual({ note: "hospital.decision.errors.note" });
    expect(validateDecision({ ...base, decision: "rejected", amountMinor: null, invoice: "", note: "Not our invoice." })).toEqual({});
  });

  it("needs a code that was sent and is six digits", () => {
    expect(validateDecision({ ...base, requestId: null }).otp).toBe("hospital.otp.needCode");
    expect(validateDecision({ ...base, otp: "123" }).otp).toBe("hospital.otp.incomplete");
  });
});

describe("compareWithBill", () => {
  it("matches the total or the balance the reader found", () => {
    expect(compareWithBill(24_500_000, "245000.00", null)).toBe("matches");
    expect(compareWithBill(5_370_000, "245000.00", "53700.00")).toBe("matches");
  });

  it("says when it differs, and stays quiet when there is nothing to compare", () => {
    expect(compareWithBill(1, "245000.00", null)).toBe("differs");
    expect(compareWithBill(null, "245000.00", null)).toBe("unknown");
    expect(compareWithBill(100, null, null)).toBe("unknown");
  });
});

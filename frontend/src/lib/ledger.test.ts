import type { PublicLedgerRow } from "@/api";
import i18n from "@/i18n";
import { ledgerLine, tranchesFrom } from "./ledger";

const row = (overrides: Partial<PublicLedgerRow>): PublicLedgerRow => ({
  created_at: "2026-10-17T09:10:00+00:00",
  entry_type: "donation_captured",
  account: "case_funds",
  amount_minor: 13000,
  amount: "130.00",
  currency: "USD",
  hash_short: "13ba904eafc1",
  reference: "SEED…0001",
  donor_name: null,
  ...overrides,
});

const t = i18n.t.bind(i18n);

describe("ledgerLine", () => {
  it("shows money in with a plus and money out with a minus sign", () => {
    expect(ledgerLine(row({}), "en", t).amount).toBe("+$130");
    expect(ledgerLine(row({ entry_type: "paypal_fee", amount_minor: -407 }), "en", t).amount).toBe("−$4.07");
  });

  it("formats shillings for the hospital's side of a payment", () => {
    const line = ledgerLine(row({ entry_type: "settlement_sent", account: "hospital_settled", currency: "KES", amount_minor: 6744507 }), "en", t);
    expect(line.amount).toBe("+KES 67,445.07");
    expect(line.account).toBe("Paid to the hospital");
  });

  it("names the donor on the donation line only", () => {
    expect(ledgerLine(row({ donor_name: "Wanjiru K." }), "en", t).what).toBe("Donation received from Wanjiru K.");
    expect(ledgerLine(row({}), "en", t).what).toBe("Donation received");
  });

  it("uses the fingerprint as the row id and leaves a missing reference empty", () => {
    const line = ledgerLine(row({ reference: null }), "en", t);
    expect(line.id).toBe("13ba904eafc1");
    expect(line.reference).toBe("");
  });
});

describe("tranchesFrom", () => {
  it("takes the shilling side of each payment to the hospital, oldest first", () => {
    const rows = [
      row({ entry_type: "settlement_sent", currency: "KES", amount_minor: 500000, created_at: "2026-10-19T10:00:00+00:00", hash_short: "b" }),
      row({ entry_type: "settlement_sent", currency: "USD", amount_minor: -3875, created_at: "2026-10-18T10:00:00+00:00", hash_short: "x" }),
      row({ entry_type: "settlement_sent", currency: "KES", amount_minor: 200000, created_at: "2026-10-18T10:00:00+00:00", hash_short: "a" }),
      row({}),
    ];
    expect(tranchesFrom(rows).map((tranche) => tranche.amountKesMinor)).toEqual([200000, 500000]);
  });
});

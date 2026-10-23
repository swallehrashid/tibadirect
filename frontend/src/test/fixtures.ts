import type { PublicCase } from "@/api";

export function publicCase(overrides: Partial<PublicCase> = {}): PublicCase {
  return {
    slug: "demolive2345",
    alias: "Baraka",
    region: "Nairobi",
    hospital_name: "Kilele Mission Hospital",
    trust_level: "hospital_confirmed",
    status: "live",
    story: "Our family is raising money to clear a hospital bill. Every shilling goes to the hospital.",
    photo_url: null,
    is_estimate: true,
    target_kes_minor: 24_000_000,
    target_kes: "240000.00",
    pledged_kes_minor: 7_204_134,
    pledged_kes: "72041.34",
    settled_kes_minor: 6_744_507,
    settled_kes: "67445.07",
    usd_goal_minor: 195_349,
    usd_goal: "1953.49",
    donor_count: 6,
    created_at: "2026-10-15T07:10:09+00:00",
    live_at: "2026-10-16T09:11:09+00:00",
    ...overrides,
  };
}

export function casePage(items: PublicCase[], extra: Partial<{ page: number; page_size: number; total: number }> = {}) {
  return { items, page: 1, page_size: 12, total: items.length, ...extra };
}

export const STATS = { public_cases: 3, open_cases: 2, pledged_kes_minor: 63_065_520, settled_kes_minor: 49_288_507, donations: 14, hospitals: 3 };

export const HOSPITALS = {
  items: [
    { id: "a", display_name: "Kilele Mission Hospital", county: "Nairobi" },
    { id: "b", display_name: "Mto Mwema Hospital", county: "Mombasa" },
    { id: "c", display_name: "Ziwa Bluu General Hospital", county: "Kisumu" },
    { id: "d", display_name: "Second Nairobi Clinic", county: "Nairobi" },
  ],
  page: 1,
  page_size: 100,
  total: 4,
};

export const FX = { base: "USD", quote: "KES", rate: "129.00000000", source: "static", fetched_at: "2026-10-23T08:00:00+00:00", is_live: false, is_stale: false };

export function ledgerRow(overrides: Record<string, unknown> = {}) {
  return {
    created_at: "2026-10-17T09:10:00+00:00",
    entry_type: "donation_captured",
    account: "case_funds",
    amount_minor: 13000,
    amount: "130.00",
    currency: "USD",
    hash_short: "13ba904eafc1",
    reference: "SEED…0001",
    donor_name: "Wanjiru K.",
    ...overrides,
  };
}

export const LEDGER = {
  items: [
    ledgerRow(),
    ledgerRow({ entry_type: "paypal_fee", amount_minor: -407, amount: "-4.07", hash_short: "25ba2f92efb5", donor_name: null }),
    ledgerRow({ entry_type: "settlement_sent", account: "hospital_settled", currency: "KES", amount_minor: 6744507, amount: "67445.07", hash_short: "54fa6ee803da", donor_name: null, created_at: "2026-10-18T14:13:00+00:00", reference: "SEED…0007" }),
  ],
  page: 1,
  page_size: 100,
  total: 3,
};

export const RECEIPT = {
  receipt_token: "tok123",
  status: "captured",
  amount_usd_minor: 2500,
  amount_usd: "25.00",
  paypal_fee_usd_minor: 105,
  paypal_fee_usd: "1.05",
  net_usd_minor: 2395,
  net_usd: "23.95",
  refunded_usd_minor: 0,
  kes_estimate_minor: 308955,
  kes_estimate: "3089.55",
  fx_rate: "129.00000000",
  excess_choice: "refund",
  donor_display_name: null,
  captured_at: "2026-10-23T10:00:00+00:00",
  case_slug: "demolive2345",
  case_alias: "Baraka",
  hospital_name: "Kilele Mission Hospital",
  ledger_reference: "MOCK…05E3",
  ledger_hash_short: "dfe246cc0754",
};

export const CASE_BASE = {
  "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "x" } } },
  "GET /api/public/fx": FX,
  "GET /api/public/cases/demolive2345/ledger": LEDGER,
};

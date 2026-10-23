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
    { id: "e1aa1af1-95c6-4886-a50b-cbb0dc490a82", display_name: "Kilele Mission Hospital", county: "Nairobi" },
    { id: "af512bca-682e-4ae1-a1a7-28e91ba63f63", display_name: "Mto Mwema Hospital", county: "Mombasa" },
    { id: "8704fb9a-f5b4-4bd7-9d08-5a5a5a5a5a5a", display_name: "Ziwa Bluu General Hospital", county: "Kisumu" },
    { id: "d4d4d4d4-4444-4444-8444-444444444444", display_name: "Second Nairobi Clinic", county: "Nairobi" },
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

export const HOSPITAL_NAME = { id: "e1aa1af1-95c6-4886-a50b-cbb0dc490a82", display_name: "Kilele Mission Hospital", county: "Nairobi" };

export const MONEY_ZERO = {
  target_kes_minor: null,
  target_kes: null,
  is_estimate: true,
  usd_goal_minor: null,
  usd_goal: null,
  pledged_kes_minor: 0,
  pledged_kes: "0.00",
  settled_kes_minor: 0,
  settled_kes: "0.00",
  captured_net_usd_minor: 0,
  captured_net_usd: "0.00",
  refunded_usd_minor: 0,
  refunded_usd: "0.00",
  donor_count: 0,
};

export function familyCase(overrides: Record<string, unknown> = {}) {
  return {
    id: "c1c1c1c1-1111-4111-8111-111111111111",
    public_slug: "zawadi01",
    alias: "Zawadi",
    region: "Nairobi",
    status: "draft",
    trust_level: "unverified",
    hospital: HOSPITAL_NAME,
    is_publicly_visible: false,
    money: MONEY_ZERO,
    created_at: "2026-10-23T08:00:00+00:00",
    story: null,
    patient_reference: "KMH-ADM-7731",
    is_minor: false,
    show_photo: false,
    consents: [],
    timeline: [{ status: "draft", at: "2026-10-23T08:00:00+00:00" }],
    triage: null,
    confirmations: [],
    is_accepting_donations: false,
    submitted_at: null,
    live_at: null,
    closed_at: null,
    updated_at: "2026-10-23T08:00:00+00:00",
    ...overrides,
  };
}

export const CASE_ID = "c1c1c1c1-1111-4111-8111-111111111111";

export const CONSENT_TEXTS = {
  language: "en",
  items: [
    { scope: "hospital_verification", language: "en", version: "v1", title: "Let the hospital confirm the bill", body: "We will send the bill details to the hospital." },
    { scope: "public_page", language: "en", version: "v1", title: "Show this case on a public page", body: "Your alias and story will be public." },
    { scope: "ai_processing_abroad", language: "en", version: "v1", title: "Allow automated checks outside Kenya", body: "Redacted text may be processed abroad." },
    { scope: "receipt_sharing", language: "en", version: "v1", title: "Share donation receipts with me", body: "We may tell you about donations." },
  ],
};

export function consentRecord(scope: string, overrides: Record<string, unknown> = {}) {
  return { id: `consent-${scope}`, scope, actor: "self", wording_version: "v1", language: "en", granted_at: "2026-10-23T08:01:00+00:00", withdrawn_at: null, active: true, ...overrides };
}

export function documentRecord(overrides: Record<string, unknown> = {}) {
  return { id: "d1d1d1d1-1111-4111-8111-111111111111", kind: "invoice", status: "uploaded", mime_type: "application/pdf", size_bytes: 120_000, page_count: 1, created_at: "2026-10-23T08:02:00+00:00", raw_available: true, preview_url: null, ...overrides };
}

export const FAMILY_BASE = {
  "GET /api/auth/me": { id: "11111111-1111-4111-8111-111111111111", email: "family1@example.test", display_name: "Demo Family 1", role: "family", preferred_language: "en", hospital: null },
  "GET /api/hospitals": HOSPITALS,
  "GET /api/consents/texts": CONSENT_TEXTS,
};

// --- Hospital portal --------------------------------------------------------------------------

export const CLERK = { id: "33333333-3333-4333-8333-333333333333", email: "clerk@kilele-hospital.test", display_name: "Kilele Mission Hospital clerk", role: "hospital_staff", preferred_language: "en", hospital: { ...HOSPITAL_NAME, staff_role: "billing_clerk" } };
export const SUPERVISOR = { ...CLERK, id: "44444444-4444-4444-8444-444444444444", email: "supervisor@kilele-hospital.test", display_name: "Kilele Mission Hospital supervisor", hospital: { ...HOSPITAL_NAME, staff_role: "billing_supervisor" } };

export const HOSPITAL_CASE_ID = "55555555-5555-4555-8555-555555555555";
export const HPATH = `/api/hospital/cases/${HOSPITAL_CASE_ID}`;

export function confirmationRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "66666666-6666-4666-8666-666666666666",
    status: "awaiting_second_approval",
    amount_kes_minor: 25_000_000,
    amount_kes: "250000.00",
    invoice_number: "KMH/26/9",
    is_final_bill: false,
    deposit_kes_minor: null,
    confirmed_by: "Kilele Mission Hospital clerk",
    second_approver: null,
    query_note: null,
    paypal_invoice_id: null,
    invoice_status: null,
    created_at: "2026-10-23T09:00:00+00:00",
    finalized_at: null,
    ...overrides,
  };
}

export function hospitalCase(overrides: Record<string, unknown> = {}) {
  return {
    id: HOSPITAL_CASE_ID,
    alias: "Zawadi",
    region: "Nairobi",
    patient_reference: "KMH-ADM-7731",
    status: "pending_confirmation",
    submitted_at: "2026-10-23T08:30:00+00:00",
    bills: [{ id: "77777777-7777-4777-8777-777777777777", kind: "invoice", mime_type: "application/pdf", page_count: 1, created_at: "2026-10-23T08:20:00+00:00", preview_url: "https://files.example.test/bill.pdf?signature=abc" }],
    triage: {
      risk_band: "low",
      reasons_en: ["The amounts add up."],
      reasons_sw: ["Viwango vinajumlishana."],
      extracted: {
        facility_name: "Kilele Mission Hospital",
        invoice_number: "KMH/26/024405",
        invoice_date: "2026-07-06",
        document_type: "invoice",
        total_kes: "160430.00",
        balance_due_kes: "160430.00",
        deposit_kes: "0.00",
        currency: "KES",
        confidence: 1,
        line_items: [
          { label: "General ward bed (per day)", amount_kes: "67200.00", quantity: 8, unit_kes: "8400.00" },
          { label: "Nursing care (per day)", amount_kes: "22800.00", quantity: 8, unit_kes: "2850.00" },
        ],
      },
      created_at: "2026-10-23T08:25:00+00:00",
    },
    confirmations: [],
    two_person_threshold_kes_minor: 20_000_000,
    otp_destination_masked: "bil***@kilele-hospital.test",
    ...overrides,
  };
}

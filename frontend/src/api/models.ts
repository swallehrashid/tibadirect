import type { CaseStatus, ConsentScope, ExcessChoice, RiskBandValue, TrustLevel, UserRole } from "./enums";
import type { components } from "./schema";

type Schemas = components["schemas"];

type DeepStrict<T> = T extends (infer U)[] ? DeepStrict<U>[] : T extends object ? { [K in keyof T]-?: DeepStrict<T[K]> } : T;

/**
 * A response object as the browser really receives it. The generated types mark every field
 * optional, because the API's schemas do not list required fields, and type computed fields as
 * `unknown`. Responses always carry every field they declare, so this removes the optional marks,
 * and `Over` supplies the concrete type of the computed fields. `Over` may only name fields that
 * exist in the schema, so a renamed or removed field becomes a compile error here.
 */
export type Model<S, Over extends { [K in keyof Over]: K extends keyof S ? unknown : never } = object> = Omit<DeepStrict<S>, keyof Over> & Over;

export type PublicConfig = Model<Schemas["PublicConfig"]>;

export type HospitalSummary = Model<Schemas["HospitalSummary"], { staff_role: "billing_clerk" | "billing_supervisor" }>;
export type User = Model<Schemas["User"], { role: UserRole; hospital: HospitalSummary | null; preferred_language: string | null }>;

export type PublicStats = Model<Schemas["PublicStats"]>;
export type PublicFx = Model<Schemas["PublicFx"]>;
export type HospitalListItem = Model<Schemas["HospitalListItem"]>;

export type PublicCase = Model<
  Schemas["PublicCase"],
  {
    hospital_name: string;
    trust_level: TrustLevel;
    status: CaseStatus;
    photo_url: string | null;
    /** Amounts as decimal strings, "1250.00"; null when the hospital has not confirmed a bill yet. */
    target_kes: string | null;
    pledged_kes: string;
    settled_kes: string;
    usd_goal: string | null;
  }
>;

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export const LEDGER_ENTRY_TYPES = ["donation_captured", "paypal_fee", "settlement_sent", "refund_issued", "pool_transfer", "reversal", "adjustment"] as const;
export type LedgerEntryType = (typeof LEDGER_ENTRY_TYPES)[number];

export type PublicLedgerRow = Model<Schemas["PublicLedgerRow"], { entry_type: LedgerEntryType; reference: string | null; donor_name: string | null }>;
export type CaseVerification = Model<Schemas["CaseVerification"]>;

export type Receipt = Model<
  Schemas["Receipt"],
  {
    amount_usd: string;
    paypal_fee_usd: string;
    net_usd: string;
    kes_estimate: string;
    excess_choice: ExcessChoice;
    status: string;
  }
>;
export type OrderCreated = Model<Schemas["OrderCreated"]>;

/** The money figures of a case, as the family and admin views carry them (minor units, plus formatted strings). */
export interface MoneySummary {
  target_kes_minor: number | null;
  target_kes: string | null;
  is_estimate: boolean;
  usd_goal_minor: number | null;
  usd_goal: string | null;
  pledged_kes_minor: number;
  pledged_kes: string;
  settled_kes_minor: number;
  settled_kes: string;
  captured_net_usd_minor: number;
  captured_net_usd: string;
  refunded_usd_minor: number;
  refunded_usd: string;
  donor_count: number;
}

export interface TimelineEntry {
  status: CaseStatus;
  at: string;
}

export type ConfirmationStatus = "awaiting_second_approval" | "confirmed" | "queried" | "rejected";

export interface ConfirmationSummary {
  status: ConfirmationStatus;
  amount_kes_minor: number | null;
  invoice_number: string | null;
  is_final_bill: boolean;
  query_note: string | null;
  created_at: string;
  finalized_at: string | null;
}

export interface TriageSummary {
  risk_band: RiskBandValue;
  route: "hospital_confirmation" | "manual_review";
  reasons_en: string[];
  reasons_sw: string[];
  created_at: string;
}

export type HospitalName = Model<Schemas["HospitalName"]>;

export type FamilyCaseListItem = Model<
  Schemas["FamilyCaseListItem"],
  { status: CaseStatus; trust_level: TrustLevel; money: MoneySummary }
>;

export type FamilyCase = Model<
  Schemas["FamilyCase"],
  {
    status: CaseStatus;
    trust_level: TrustLevel;
    money: MoneySummary;
    consents: ConsentScope[];
    timeline: TimelineEntry[];
    triage: TriageSummary | null;
    confirmations: ConfirmationSummary[];
    is_accepting_donations: boolean;
    story: string | null;
    submitted_at: string | null;
    live_at: string | null;
    closed_at: string | null;
  }
>;

export type DocumentKind = "invoice" | "estimate" | "final_bill" | "photo";
export type DocumentStatus = "uploaded" | "processing" | "triaged" | "failed";

export type CaseDocument = Model<
  Schemas["Document"],
  { kind: DocumentKind; status: DocumentStatus; raw_available: boolean; preview_url: string | null; page_count: number | null }
>;

export type ConsentText = Model<Schemas["ConsentText"], { scope: ConsentScope; language: "en" | "sw" }>;
export type Consent = Model<Schemas["Consent"], { scope: ConsentScope; actor: "self" | "guardian"; withdrawn_at: string | null }>;
export type TriageStatus = Model<Schemas["TriageStatus"], { risk_band: RiskBandValue | null; route: "hospital_confirmation" | "manual_review" | null; checked_at: string | null }>;

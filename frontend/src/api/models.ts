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

export type DocumentKind = "invoice" | "estimate" | "final_bill" | "photo";

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

export type DocumentStatus = "uploaded" | "processing" | "triaged" | "failed";

export type CaseDocument = Model<
  Schemas["Document"],
  { kind: DocumentKind; status: DocumentStatus; raw_available: boolean; preview_url: string | null; page_count: number | null }
>;

export type ConsentText = Model<Schemas["ConsentText"], { scope: ConsentScope; language: "en" | "sw" }>;
export type Consent = Model<Schemas["Consent"], { scope: ConsentScope; actor: "self" | "guardian"; withdrawn_at: string | null }>;
export type TriageStatus = Model<Schemas["TriageStatus"], { risk_band: RiskBandValue | null; route: "hospital_confirmation" | "manual_review" | null; checked_at: string | null }>;

// --- Hospital portal -------------------------------------------------------------------------

export type HospitalConfirmation = Model<
  Schemas["Confirmation"],
  {
    status: ConfirmationStatus;
    amount_kes: string | null;
    confirmed_by: string;
    second_approver: string | null;
    deposit_kes_minor: number | null;
  }
>;

export type QueueConfirmation = Model<
  Schemas["QueueConfirmation"],
  {
    status: ConfirmationStatus;
    amount_kes: string | null;
    confirmed_by: string;
    second_approver: string | null;
    deposit_kes_minor: number | null;
    case_id: string;
    case_alias: string;
  }
>;

export type QueueCase = Model<Schemas["QueueCase"], { risk_band: RiskBandValue | null; kind: "first_bill" | "final_bill" }>;

export interface HospitalQueue {
  pending: QueueCase[];
  awaiting_second_approval: QueueConfirmation[];
}

export interface BillLine {
  label: string;
  amount_kes: string;
  quantity: number | null;
  unit_kes: string | null;
}

/** What the reader found on the bill. Every field may be missing when the bill could not be read. */
export interface ExtractedFields {
  facility_name?: string | null;
  invoice_number?: string | null;
  invoice_date?: string | null;
  document_type?: "invoice" | "estimate" | "final_bill" | "unknown";
  line_items?: BillLine[];
  total_kes?: string | null;
  balance_due_kes?: string | null;
  deposit_kes?: string | null;
  currency?: "KES" | "USD" | "unknown";
  confidence?: number;
}

export interface HospitalBill {
  id: string;
  kind: DocumentKind;
  mime_type: string;
  page_count: number | null;
  created_at: string;
  preview_url: string | null;
}

export interface HospitalTriage {
  risk_band: RiskBandValue;
  reasons_en: string[];
  reasons_sw: string[];
  extracted: ExtractedFields;
  created_at: string;
}

export type HospitalCaseDetail = Model<
  Schemas["HospitalCaseDetail"],
  {
    status: CaseStatus;
    bills: HospitalBill[];
    triage: HospitalTriage | null;
    confirmations: HospitalConfirmation[];
    two_person_threshold_kes_minor: number;
    otp_destination_masked: string;
  }
>;

export type OtpSent = Model<Schemas["OtpSent"]>;
export type SettlementStatus = "pending" | "processing" | "succeeded" | "failed";
export type SettlementItem = Model<Schemas["SettlementItem"], { case_alias: string; status: SettlementStatus; amount_kes: string; external_reference: string | null }>;

// --- Admin console ---------------------------------------------------------------------------

export interface QueueStats {
  name: string;
  queued: number;
  started: number;
  scheduled: number;
  failed: number;
}

export interface FailedJob {
  id: string;
  queue: string;
  function: string;
  enqueued_at: string | null;
  failed_at: string | null;
  retries_left: number;
  request_id: string | null;
  error: string;
}

export interface JobsOverview {
  queues: QueueStats[];
  failed_jobs: FailedJob[];
  scheduler: { last_heartbeat: string | null; age_seconds: number | null; healthy: boolean };
}

export interface AdminStats {
  cases_by_status: Partial<Record<CaseStatus, number>>;
  cases_total: number;
  money: {
    captured_net_usd_minor: number;
    refunded_usd_minor: number;
    settled_usd_minor: number;
    pledged_kes_minor: number;
    settled_kes_minor: number;
    donations: number;
  };
  waiting: { open_review_items: number; awaiting_second_approval: number };
  users: Record<string, number>;
  ledger_entries: number;
  queues: QueueStats[] | null;
}

export interface ChainVerification {
  valid: boolean;
  entries_checked: number;
  last_hash: string;
  first_bad_seq: number | null;
  checked_at: string;
}

export type ReviewSource = "ai_flag" | "family_request" | "insider_anomaly" | "admin";
export type ReviewResolution = "approved_for_confirmation" | "kept_unverified" | "case_cancelled" | "no_action";

export interface ReviewItem {
  id: string;
  case_id: string;
  case_alias: string;
  case_status: CaseStatus;
  source: ReviewSource;
  status: "open" | "resolved";
  reason: string;
  payload: Record<string, unknown>;
  resolution: ReviewResolution | null;
  resolution_note: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface AdminCaseListItem {
  id: string;
  public_slug: string;
  alias: string;
  status: CaseStatus;
  trust_level: TrustLevel;
  hospital_name: string;
  owner_email: string;
  target_kes_minor: number | null;
  pledged_kes_minor: number;
  settled_kes_minor: number;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: string;
  created_at: string;
  actor_type: "user" | "system" | "agent" | "paypal";
  actor_user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  details: Record<string, unknown>;
  request_id: string | null;
}

export interface AdminTriage {
  document_id: string;
  risk_score: number;
  risk_band: RiskBandValue;
  route: "hospital_confirmation" | "manual_review";
  extracted: ExtractedFields;
  signals: { code?: string; type?: string; [key: string]: unknown }[];
  reasons_en: string[];
  used_llm: boolean;
  model_versions: Record<string, string>;
  created_at: string;
}

export interface AdminDonation {
  id: string;
  status: string;
  amount_usd_minor: number;
  paypal_fee_usd_minor: number | null;
  net_usd_minor: number;
  allocated_usd_minor: number;
  refunded_usd_minor: number;
  excess_choice: ExcessChoice | null;
  donor_display_name: string | null;
  is_anonymous: boolean;
  captured_at: string | null;
}

export interface AdminSettlement {
  id: string;
  tranche_no: number;
  status: SettlementStatus;
  adapter: string;
  amount_usd_minor: number;
  amount_kes_minor: number;
  external_batch_id: string | null;
  failure_reason: string | null;
  requested_at: string;
  completed_at: string | null;
}

export interface AdminRefund {
  id: string;
  donation_id: string;
  amount_usd_minor: number;
  reason: string;
  status: string;
  requested_by: string;
  completed_at: string | null;
}

export interface AdminCase {
  id: string;
  public_slug: string;
  alias: string;
  region: string;
  story: string | null;
  patient_reference: string;
  is_minor: boolean;
  status: CaseStatus;
  trust_level: TrustLevel;
  close_reason: string | null;
  freeze_reason: string | null;
  owner: { id: string; email: string; display_name: string };
  hospital: { id: string; display_name: string };
  money: MoneySummary;
  documents: CaseDocument[];
  triage: AdminTriage[];
  consents: { scope: ConsentScope; actor: string; wording_version: string; language: string; granted_at: string; withdrawn_at: string | null }[];
  confirmations: HospitalConfirmation[];
  donations: AdminDonation[];
  settlements: AdminSettlement[];
  refunds: AdminRefund[];
  review_items: ReviewItem[];
  audit_trail: AuditEntry[];
  created_at: string;
  submitted_at: string | null;
  live_at: string | null;
  closed_at: string | null;
  frozen_at: string | null;
}

export interface SettlementRun {
  reason: string;
  settlement_id: string | null;
  tranche_no: number | null;
  amount_usd_minor: number | null;
  amount_kes_minor: number | null;
  status: SettlementStatus | null;
}

export type AgentActionType = "refund" | "payout" | "freeze";
export type AgentActionStatus = "proposed" | "approved" | "rejected" | "executed" | "failed";

export interface AgentAction {
  id: string;
  action_type: AgentActionType;
  case_id: string | null;
  case_alias: string | null;
  payload: Record<string, unknown>;
  rationale: string;
  status: AgentActionStatus;
  decided_by: string | null;
  decided_at: string | null;
  executed_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
}

export interface AgentToolCall {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

export interface AgentAnswer {
  answer: string;
  tool_calls: AgentToolCall[];
  proposed_actions: AgentAction[];
  steps: number;
  stopped_early: boolean;
}

// --- Appeal checker --------------------------------------------------------------------------

export interface MatchedCase {
  slug: string;
  alias: string;
  hospital_name: string;
  url: string;
  note: string;
}

export interface AppealResult {
  score: number;
  band: RiskBandValue;
  flagged: boolean;
  reasons_en: string[];
  reasons_sw: string[];
  matched_case: MatchedCase | null;
  safety_tip_en: string;
  safety_tip_sw: string;
  explanation: string | null;
  used_llm: boolean;
}

/** Values the API sends for its enumerations (backend/app/models/enums.py). */
export const TRUST_LEVELS = ["unverified", "pending_confirmation", "hospital_confirmed"] as const;
export type TrustLevel = (typeof TRUST_LEVELS)[number];

export const RISK_BANDS = ["low", "medium", "high"] as const;
export type RiskBandValue = (typeof RISK_BANDS)[number];

export const CASE_STATUSES = [
  "draft",
  "pending_triage",
  "in_review",
  "pending_confirmation",
  "live",
  "funded",
  "closed",
  "cancelled",
  "frozen",
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

export const CONSENT_SCOPES = ["hospital_verification", "public_page", "ai_processing_abroad", "receipt_sharing"] as const;
export type ConsentScope = (typeof CONSENT_SCOPES)[number];

export const EXCESS_CHOICES = ["refund", "solidarity_pool", "follow_up_care"] as const;
export type ExcessChoice = (typeof EXCESS_CHOICES)[number];

export const USER_ROLES = ["family", "hospital_staff", "admin"] as const;
export type UserRole = (typeof USER_ROLES)[number];

export function isOneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

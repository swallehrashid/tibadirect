import type { Decision } from "@/api/hospital";
import { parseMinor } from "./money";

export const MAX_BILL_KES_MINOR = 5_000_000_000; // KES 50 million: above this the server assumes a typing error
export const INVOICE_NUMBER_MAX = 60;
export const NOTE_MIN = 5;
export const NOTE_MAX = 500;

export interface DecisionValues {
  decision: Decision;
  amountMinor: number | null;
  invoice: string;
  depositMinor: number | null;
  note: string;
  otp: string;
  requestId: string | null;
}

export type DecisionField = "amount" | "invoice" | "deposit" | "note" | "otp";

/** Which fields are wrong, as translation keys. The server checks again; this saves a code attempt. */
export function validateDecision(values: DecisionValues): Partial<Record<DecisionField, string>> {
  const errors: Partial<Record<DecisionField, string>> = {};
  if (values.decision === "confirmed") {
    if (values.amountMinor === null || values.amountMinor <= 0) errors.amount = "hospital.decision.errors.amount";
    else if (values.amountMinor > MAX_BILL_KES_MINOR) errors.amount = "hospital.decision.errors.amountLarge";
    const invoice = values.invoice.trim().replace(/\s+/g, " ");
    if (!invoice || invoice.length > INVOICE_NUMBER_MAX) errors.invoice = "hospital.decision.errors.invoice";
    if (values.depositMinor !== null && (values.amountMinor === null || values.depositMinor > values.amountMinor)) errors.deposit = "hospital.decision.errors.deposit";
  } else if (values.note.trim().length < NOTE_MIN) {
    errors.note = "hospital.decision.errors.note";
  }
  if (!values.requestId) errors.otp = "hospital.otp.needCode";
  else if (!/^\d{6}$/.test(values.otp)) errors.otp = "hospital.otp.incomplete";
  return errors;
}

export type BillComparison = "matches" | "differs" | "unknown";

/** Does what was typed agree with the total (or balance) the reader found? A hint for the clerk, never a gate. */
export function compareWithBill(typedMinor: number | null, total: string | null | undefined, balance: string | null | undefined): BillComparison {
  if (typedMinor === null) return "unknown";
  const candidates = [total, balance].map((value) => (value ? parseMinor(value) : null)).filter((value): value is number => value !== null);
  if (candidates.length === 0) return "unknown";
  return candidates.includes(typedMinor) ? "matches" : "differs";
}

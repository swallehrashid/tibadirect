import type { PublicLedgerRow } from "@/api";
import { formatMoney, type Currency } from "./money";

type Translate = (key: string, options?: Record<string, unknown>) => string;

export interface LedgerLine {
  /** Stable key: the entry's fingerprint is unique. */
  id: string;
  createdAt: string;
  what: string;
  account: string;
  /** "+$25", "−$0.80", "+KES 67,445.07": the sign shows which way the money moved in that account. */
  amount: string;
  amountMinor: number;
  currency: Currency;
  reference: string;
  fingerprint: string;
}

const MINUS = "−"; // a real minus sign reads better than a hyphen next to numbers

export function ledgerLine(row: PublicLedgerRow, language: string, t: Translate): LedgerLine {
  const currency: Currency = row.currency === "KES" ? "KES" : "USD";
  const sign = row.amount_minor < 0 ? MINUS : "+";
  const base = t(`ledger.types.${row.entry_type}`, { defaultValue: row.entry_type });
  return {
    id: row.hash_short,
    createdAt: row.created_at,
    what: row.donor_name ? t("ledger.fromDonor", { what: base, name: row.donor_name }) : base,
    account: t(`ledger.accounts.${row.account}`, { defaultValue: row.account }),
    amount: `${sign}${formatMoney(Math.abs(row.amount_minor), currency, language)}`,
    amountMinor: row.amount_minor,
    currency,
    reference: row.reference ?? "",
    fingerprint: row.hash_short,
  };
}

export interface Tranche {
  at: string;
  amountKesMinor: number;
  reference: string | null;
}

/** Payments to the hospital, oldest first: the KES side of each settlement entry. */
export function tranchesFrom(rows: PublicLedgerRow[]): Tranche[] {
  return rows
    .filter((row) => row.entry_type === "settlement_sent" && row.currency === "KES" && row.amount_minor > 0)
    .map((row) => ({ at: row.created_at, amountKesMinor: row.amount_minor, reference: row.reference }))
    .sort((a, b) => a.at.localeCompare(b.at));
}

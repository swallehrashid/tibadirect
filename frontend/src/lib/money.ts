/**
 * Money is held as integers in minor units (cents) everywhere, as in the API. Floats are only
 * used to show a value, never to add it up.
 */
export type Currency = "KES" | "USD";

function localeFor(language: string | undefined): string {
  return language?.startsWith("sw") ? "sw-KE" : "en-KE";
}

/** "KES 12,500" or "KES 12,500.50"; whole amounts drop the cents. */
export function formatKes(minor: number, language?: string): string {
  const whole = minor % 100 === 0;
  const number = new Intl.NumberFormat(localeFor(language), {
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(minor / 100);
  return `KES ${number}`;
}

/** "$25" or "$25.50". */
export function formatUsd(minor: number): string {
  const whole = minor % 100 === 0;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(minor / 100);
}

export function formatMoney(minor: number, currency: Currency, language?: string): string {
  return currency === "KES" ? formatKes(minor, language) : formatUsd(minor);
}

/**
 * Reads what a person typed ("25", "25.5", "1,250.00") into minor units. Returns null when it
 * is not a plain amount with at most two decimals. Commas are thousands separators.
 */
export function parseMinor(text: string): number | null {
  const cleaned = text.trim().replace(/,/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  const [whole = "0", fraction = ""] = cleaned.split(".");
  const minor = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return Number.isSafeInteger(minor) ? minor : null;
}

/** "12.50" for a field value; empty when there is no amount yet. */
export function minorToInput(minor: number | null): string {
  if (minor === null) return "";
  return minor % 100 === 0 ? String(minor / 100) : (minor / 100).toFixed(2);
}

/** Percentage (0 to 100) of a target, rounded down to a whole number and kept in range. */
export function percentOf(part: number, target: number | null | undefined): number {
  if (!target || target <= 0 || part <= 0) return 0;
  return Math.min(100, Math.floor((part * 100) / target));
}

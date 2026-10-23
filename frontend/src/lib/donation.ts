/** Donation amounts are in US cents. The server enforces $1 to $10,000 as well. */
export const PRESETS_USD_MINOR = [1000, 2500, 5000, 10000] as const;
export const MIN_USD_MINOR = 100;
export const MAX_USD_MINOR = 1_000_000;

export function validAmount(minor: number | null): minor is number {
  return minor !== null && minor >= MIN_USD_MINOR && minor <= MAX_USD_MINOR;
}

/** KES for an amount of US cents at a rate such as "129.00000000". Display only; the receipt uses the rate at payment time. */
export function kesEstimateMinor(usdMinor: number, rate: string | undefined): number | null {
  const value = Number(rate);
  return Number.isFinite(value) && value > 0 ? Math.round(usdMinor * value) : null;
}

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

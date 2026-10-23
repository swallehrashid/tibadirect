import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { isApiError } from "./errors";
import type { CaseVerification, HospitalListItem, Page, PublicCase, PublicConfig, PublicFx, PublicLedgerRow, PublicStats, Receipt } from "./models";

/** Browser settings from the server (PayPal client ID, sandbox/demo flags). Loaded once. */
export function usePublicConfig() {
  return useQuery({
    queryKey: ["public", "config"],
    queryFn: async () => unwrap(await api.GET("/api/public/config")) as PublicConfig,
    staleTime: Infinity,
  });
}

export function usePublicStats() {
  return useQuery({
    queryKey: ["public", "stats"],
    queryFn: async () => unwrap(await api.GET("/api/public/stats")) as PublicStats,
    staleTime: 60_000,
  });
}

export const PUBLIC_CASES_PAGE_SIZE = 12;

/** Live and funded public cases, newest first, optionally narrowed to a hospital county. */
export function usePublicCases(options: { county?: string | null; page?: number; pageSize?: number } = {}) {
  const { county = null, page = 1, pageSize = PUBLIC_CASES_PAGE_SIZE } = options;
  return useQuery({
    queryKey: ["public", "cases", { county, page, pageSize }],
    queryFn: async () =>
      unwrap(await api.GET("/api/public/cases", { params: { query: { county: county ?? undefined, page, page_size: pageSize } } })) as unknown as Page<PublicCase>,
    staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
}

/** Active hospitals (name and county). The browse page builds its county filter from them. */
export function useHospitals() {
  return useQuery({
    queryKey: ["hospitals"],
    queryFn: async () => unwrap(await api.GET("/api/hospitals", { params: { query: { page_size: 100 } } })).items as HospitalListItem[],
    staleTime: 10 * 60_000,
  });
}

export function usePublicCase(slug: string) {
  return useQuery({
    queryKey: ["public", "case", slug],
    queryFn: async () => unwrap(await api.GET("/api/public/cases/{slug}", { params: { path: { slug } } })) as PublicCase,
    // Money figures move while people give: always show fresh numbers on arrival, then keep them fresh.
    staleTime: 0,
    refetchOnMount: "always",
    refetchInterval: 30_000,
    retry: (count, error) => !(isApiError(error) && error.status < 500) && count < 2,
  });
}

export function usePublicLedger(slug: string, page = 1) {
  return useQuery({
    queryKey: ["public", "ledger", slug, page],
    queryFn: async () =>
      unwrap(await api.GET("/api/public/cases/{slug}/ledger", { params: { path: { slug }, query: { page, page_size: 100 } } })) as unknown as Page<PublicLedgerRow>,
    refetchInterval: 30_000,
  });
}

/** Recomputes the case's hash chain on the server. Only runs when asked (the verify button). */
export function useLedgerVerification(slug: string) {
  return useQuery({
    queryKey: ["public", "ledger-verify", slug],
    queryFn: async () => unwrap(await api.GET("/api/public/cases/{slug}/ledger/verify", { params: { path: { slug } } })) as CaseVerification,
    enabled: false,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

export function useFx() {
  return useQuery({
    queryKey: ["public", "fx"],
    queryFn: async () => unwrap(await api.GET("/api/public/fx")) as PublicFx,
    staleTime: 5 * 60_000,
  });
}

export function useReceipt(token: string) {
  return useQuery({
    queryKey: ["receipt", token],
    queryFn: async () => unwrap(await api.GET("/api/donations/receipts/{receipt_token}", { params: { path: { receipt_token: token } } })) as unknown as Receipt,
    retry: (count, error) => !(isApiError(error) && error.status < 500) && count < 2,
  });
}

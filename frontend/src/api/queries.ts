import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { HospitalListItem, Page, PublicCase, PublicConfig, PublicStats } from "./models";

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

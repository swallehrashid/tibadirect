import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { PublicConfig } from "./models";

/** Browser settings from the server (PayPal client ID, sandbox/demo flags). Loaded once. */
export function usePublicConfig() {
  return useQuery({
    queryKey: ["public", "config"],
    queryFn: async () => unwrap(await api.GET("/api/public/config")) as PublicConfig,
    staleTime: Infinity,
  });
}

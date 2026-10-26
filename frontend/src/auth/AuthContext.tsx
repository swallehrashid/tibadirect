import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, forgetCsrfToken, isApiError, unwrap, unwrapEmpty } from "@/api";
import type { User } from "@/api/models";
import i18n from "@/i18n";

type Status = "loading" | "authenticated" | "anonymous";

interface AuthValue {
  user: User | null;
  status: Status;
  /** Call after a successful sign-in with the user the API returned. */
  signedIn: (user: User) => void;
  signOut: () => Promise<void>;
  /** True after the person chose to sign out, so guarded pages send them home rather than to sign-in. */
  leftOnPurpose: boolean;
}

const AuthContext = createContext<AuthValue | null>(null);
const ME_KEY = ["auth", "me"] as const;

/** Drops everything cached for the previous session but keeps the session query itself, which is still being watched. */
function forgetSessionData(queryClient: QueryClient) {
  queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [leftOnPurpose, setLeftOnPurpose] = useState(false);

  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: async (): Promise<User | null> => {
      const result = await api.GET("/api/auth/me");
      if (result.response.status === 401) return null;
      return unwrap(result) as unknown as User;
    },
    staleTime: 5 * 60_000,
    retry: false,
  });

  const signedIn = useCallback(
    (user: User) => {
      // The CSRF token belongs to the old session; the next write fetches a fresh one.
      forgetCsrfToken();
      forgetSessionData(queryClient);
      setLeftOnPurpose(false);
      queryClient.setQueryData(ME_KEY, user);
    },
    [queryClient],
  );

  const logout = useMutation({
    mutationFn: async () => unwrapEmpty(await api.POST("/api/auth/logout")),
    onSettled: () => {
      forgetCsrfToken();
      forgetSessionData(queryClient);
      queryClient.setQueryData(ME_KEY, null);
    },
  });

  const user = me.data ?? null;

  // Keep the saved language preference in step with the toggle, quietly, for signed-in people.
  useEffect(() => {
    if (!user) return;
    const onChange = (language: string) => {
      const code = language.startsWith("sw") ? "sw" : "en";
      if (user.preferred_language === code) return;
      void api
        .PATCH("/api/auth/me", { body: { preferred_language: code } })
        .then(() => queryClient.setQueryData(ME_KEY, { ...user, preferred_language: code }))
        .catch(() => undefined); // a failed preference save is not worth interrupting anyone
    };
    i18n.on("languageChanged", onChange);
    return () => i18n.off("languageChanged", onChange);
  }, [user, queryClient]);

  const status: Status = me.isPending ? "loading" : user ? "authenticated" : "anonymous";
  const value = useMemo<AuthValue>(
    () => ({
      user,
      status,
      signedIn,
      leftOnPurpose,
      signOut: async () => {
        setLeftOnPurpose(true);
        await logout.mutateAsync().catch(() => undefined);
      },
    }),
    [user, status, signedIn, leftOnPurpose, logout],
  );
  if (me.isError && !isApiError(me.error)) {
    // Network trouble while checking the session: treat as signed out; pages show their own errors.
    return <AuthContext.Provider value={{ ...value, status: "anonymous" }}>{children}</AuthContext.Provider>;
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

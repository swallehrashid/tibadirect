import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, unwrap } from "@/api";
import type { User } from "@/api/models";
import { useAuth } from "@/auth/AuthContext";
import { safeNext } from "@/auth/roles";
import { LOGIN_EMAIL_KEY, signInErrorMessage } from "@/auth/signIn";
import { OtpInput } from "@/components/OtpInput";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/toaster";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import i18n, { hasStoredLanguage, setLanguage } from "@/i18n";
import { usePageTitle } from "@/lib/usePageTitle";

const RESEND_SECONDS = 30;

function savedEmail(state: unknown): string | null {
  if (state && typeof state === "object" && "email" in state && typeof state.email === "string") return state.email;
  try {
    return sessionStorage.getItem(LOGIN_EMAIL_KEY);
  } catch {
    return null;
  }
}

export function LoginVerify() {
  const { t } = useTranslation();
  usePageTitle(t("auth.verifyTitle"));
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { user, signedIn } = useAuth();
  const email = savedEmail(location.state);
  const [code, setCode] = useState("");
  const [attempt, setAttempt] = useState(0); // remounts the boxes after a wrong code, so focus returns to the first
  const [cooldown, setCooldown] = useState(RESEND_SECONDS);
  const next = params.get("next");
  const submitted = useRef<string | null>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const verify = useMutation({
    mutationFn: async (value: string) => unwrap(await api.POST("/api/auth/verify", { body: { email: email ?? "", code: value } })),
    onSuccess: (data) => {
      const signedInUser = data.user as unknown as User;
      try {
        sessionStorage.removeItem(LOGIN_EMAIL_KEY);
      } catch {
        // nothing to clear
      }
      signedIn(signedInUser);
      const preferred = signedInUser.preferred_language;
      if ((preferred === "en" || preferred === "sw") && !hasStoredLanguage() && !i18n.language.startsWith(preferred)) setLanguage(preferred);
      navigate(safeNext(next, signedInUser.role), { replace: true });
    },
    onError: () => {
      submitted.current = null;
      setCode("");
      setAttempt((value) => value + 1);
    },
  });

  const resend = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/auth/request-code", { body: { email: email ?? "" } })),
    onSuccess: () => {
      setCooldown(RESEND_SECONDS);
      toast.success(t("auth.resent"));
    },
  });

  if (user) return <Navigate to={safeNext(next, user.role)} replace />;
  if (!email) return <Navigate to={`/login${next ? `?next=${encodeURIComponent(next)}` : ""}`} replace />;

  function submit(value: string) {
    if (value.length !== 6 || submitted.current === value || verify.isPending) return;
    submitted.current = value;
    verify.mutate(value);
  }

  const error = verify.isError ? signInErrorMessage(verify.error, t) : resend.isError ? signInErrorMessage(resend.error, t) : null;

  return (
    <div className="container flex justify-center py-10 md:py-16">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="font-heading text-2xl">{t("auth.verifyTitle")}</CardTitle>
          <CardDescription>{t("auth.verifyIntro", { email })}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              submit(code);
            }}
            className="space-y-4"
          >
            <OtpInput key={attempt} value={code} onChange={setCode} onComplete={submit} focusOnMount invalid={verify.isError} describedBy={error ? "code-error" : undefined} disabled={verify.isPending} />
            {error ? (
              <Alert tone="danger" id="code-error">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Button type="submit" className="w-full" disabled={code.length !== 6 || verify.isPending}>
              {verify.isPending ? t("common.loading") : t("auth.signIn")}
            </Button>
          </form>
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <Button variant="link" className="h-auto p-0" disabled={cooldown > 0 || resend.isPending} onClick={() => resend.mutate()}>
              {cooldown > 0 ? t("auth.resendIn", { seconds: cooldown }) : t("auth.resend")}
            </Button>
            <Link to={`/login${next ? `?next=${encodeURIComponent(next)}` : ""}`} className="font-semibold text-primary underline-offset-4 hover:underline">
              {t("auth.otherEmail")}
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export const Component = LoginVerify;

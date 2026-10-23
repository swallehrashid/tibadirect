import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { api, usePublicConfig } from "@/api";
import { unwrap } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { safeNext } from "@/auth/roles";
import { LOGIN_EMAIL_KEY, signInErrorMessage } from "@/auth/signIn";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePageTitle } from "@/lib/usePageTitle";

export function Login() {
  const { t } = useTranslation();
  usePageTitle(t("auth.signInTitle"));
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const { data: config } = usePublicConfig();
  const next = params.get("next");

  const schema = z.object({ email: z.string().trim().min(1, t("auth.emailRequired")).email(t("auth.emailInvalid")) });
  type Values = z.infer<typeof schema>;
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "" } });

  const request = useMutation({
    mutationFn: async (values: Values) => unwrap(await api.POST("/api/auth/request-code", { body: { email: values.email } })),
    onSuccess: (_data, values) => {
      try {
        sessionStorage.setItem(LOGIN_EMAIL_KEY, values.email);
      } catch {
        // Without storage the code screen is reached by navigation state only.
      }
      navigate(`/login/verify${next ? `?next=${encodeURIComponent(next)}` : ""}`, { state: { email: values.email } });
    },
  });

  if (user) return <Navigate to={safeNext(next, user.role)} replace />;

  return (
    <div className="container flex justify-center py-10 md:py-16">
      <Card className="w-full max-w-md">
        <CardHeader>
          <h1 className="font-heading text-2xl font-semibold leading-snug">{t("auth.signInTitle")}</h1>
          <CardDescription>{t("auth.signInIntro")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={form.handleSubmit((values) => request.mutate(values))} noValidate className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">{t("auth.email")}</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                inputMode="email"
                aria-invalid={form.formState.errors.email ? true : undefined}
                aria-describedby={form.formState.errors.email ? "email-error" : undefined}
                {...form.register("email")}
              />
              {form.formState.errors.email ? (
                <p id="email-error" role="alert" className="text-sm font-semibold text-danger-700">
                  {form.formState.errors.email.message}
                </p>
              ) : null}
            </div>
            {request.isError ? (
              <Alert tone="danger">
                <AlertDescription>{signInErrorMessage(request.error, t)}</AlertDescription>
              </Alert>
            ) : null}
            <Button type="submit" className="w-full" disabled={request.isPending}>
              <Mail /> {request.isPending ? t("common.loading") : t("auth.sendCode")}
            </Button>
          </form>
          <p className="mt-4 text-sm text-muted-foreground">{t("auth.noPassword")}</p>
          {config?.demo_banner ? (
            <Alert tone="info" className="mt-4">
              <div className="space-y-1">
                <p className="font-semibold">{t("auth.demoTitle")}</p>
                <p>{t("auth.demoBody")}</p>
                <ul className="list-disc pl-5">
                  <li>
                    <code>family1@example.test</code> ({t("shell.area.family")})
                  </li>
                  <li>
                    <code>admin@tibadirect.test</code> ({t("shell.area.admin")})
                  </li>
                </ul>
              </div>
            </Alert>
          ) : null}
          <p className="mt-6 text-sm">
            <Link to="/" className="font-semibold text-primary underline-offset-4 hover:underline">
              {t("auth.backHome")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export const Component = Login;

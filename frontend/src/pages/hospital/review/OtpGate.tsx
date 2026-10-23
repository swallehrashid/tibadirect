import { useMutation } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/api";
import { hospitalApi, type OtpPurpose } from "@/api/hospital";
import { OtpInput } from "@/components/OtpInput";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

const RESEND_SECONDS = 30;

interface Props {
  caseId: string;
  purpose: OtpPurpose;
  /** The register's official address, masked. Codes go there and nowhere else. */
  destinationMasked: string;
  code: string;
  onCode: (code: string) => void;
  /** Id of the code request, or null until a code has been sent (or after one stops working). */
  onRequest: (requestId: string | null) => void;
  requestId: string | null;
  /** A message about the code from the last attempt to use it. */
  error?: string | null;
  disabled?: boolean;
}

/** Sends the one-time code to the hospital's official address and collects it. */
export function OtpGate({ caseId, purpose, destinationMasked, code, onCode, onRequest, requestId, error = null, disabled = false }: Props) {
  const { t, i18n } = useTranslation();
  const [expires, setExpires] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((seconds) => seconds - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const send = useMutation({
    mutationFn: () => hospitalApi.sendOtp(caseId, purpose),
    onSuccess: (sent) => {
      onCode("");
      onRequest(sent.request_id);
      setExpires(sent.expires_at);
      setCooldown(RESEND_SECONDS);
    },
  });

  const sendError = send.isError ? (isApiError(send.error) && send.error.status === 429 ? t("hospital.otp.tooMany") : isApiError(send.error, "otp_not_sent") ? t("hospital.otp.notSent") : isApiError(send.error) && send.error.status === 409 ? t("hospital.otp.notWaiting") : t("error.body")) : null;
  const time = expires ? new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { hour: "2-digit", minute: "2-digit" }).format(new Date(expires)) : "";

  if (!requestId) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">{t("hospital.otp.intro", { address: destinationMasked })}</p>
        {error ? (
          <p id={`otp-error-${purpose}`} role="alert" className="text-sm font-semibold text-danger-700">
            {error}
          </p>
        ) : null}
        {sendError ? (
          <Alert tone="danger">
            <AlertDescription>{sendError}</AlertDescription>
          </Alert>
        ) : null}
        <Button onClick={() => send.mutate()} disabled={send.isPending || disabled}>
          <Mail /> {send.isPending ? t("common.loading") : t("hospital.otp.send", { address: destinationMasked })}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm" aria-live="polite">
        {t("hospital.otp.sent", { address: destinationMasked, time })}
      </p>
      <OtpInput value={code} onChange={onCode} invalid={Boolean(error)} describedBy={error ? `otp-error-${purpose}` : undefined} disabled={disabled} label={t("hospital.otp.label")} />
      {error ? (
        <p id={`otp-error-${purpose}`} role="alert" className="text-sm font-semibold text-danger-700">
          {error}
        </p>
      ) : null}
      {sendError ? (
        <Alert tone="danger">
          <AlertDescription>{sendError}</AlertDescription>
        </Alert>
      ) : null}
      <Button variant="link" className="h-auto p-0" disabled={cooldown > 0 || send.isPending || disabled} onClick={() => send.mutate()}>
        {cooldown > 0 ? t("hospital.otp.resendIn", { seconds: cooldown }) : t("hospital.otp.resend")}
      </Button>
    </div>
  );
}

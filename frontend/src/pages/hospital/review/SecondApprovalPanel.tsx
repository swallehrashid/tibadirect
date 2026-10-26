import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError, type HospitalCaseDetail, type HospitalConfirmation } from "@/api";
import { hospitalApi } from "@/api/hospital";
import { useAuth } from "@/auth/AuthContext";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "@/components/ui/toaster";
import { formatKes } from "@/lib/money";
import { OtpGate } from "./OtpGate";
import type { Outcome } from "./OutcomeCard";

/** A large bill needs a second person. A different supervisor approves it here with a fresh code. */
export function SecondApprovalPanel({ detail, confirmation, onDone }: { detail: HospitalCaseDetail; confirmation: HospitalConfirmation; onDone: (outcome: Outcome) => void }) {
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [otp, setOtp] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [otpError, setOtpError] = useState<string | null>(null);
  const supervisor = user?.hospital?.staff_role === "billing_supervisor";
  const sameUser = confirmation.confirmed_by === user?.display_name;

  const approve = useMutation({
    mutationFn: () => hospitalApi.approve(confirmation.id, { requestId: requestId!, otp }),
    onSuccess: async () => {
      toast.success(t("hospital.second.approved"));
      onDone({ kind: "approved" });
      await queryClient.invalidateQueries({ queryKey: ["hospital"] });
    },
    onError: (error) => {
      if (isApiError(error, "otp_invalid")) setOtpError(t("hospital.otp.wrong"));
      else if (isApiError(error, "otp_expired")) {
        setOtpError(t("hospital.otp.expired"));
        setRequestId(null);
      } else if (isApiError(error, "otp_locked")) {
        setOtpError(t("hospital.otp.locked"));
        setRequestId(null);
      } else if (isApiError(error, "same_person_cannot_approve")) setOtpError(t("hospital.second.samePerson"));
      else if (isApiError(error, "supervisor_required")) setOtpError(t("hospital.second.needSupervisor"));
      else setOtpError(t("error.body"));
    },
  });

  return (
    <Card className="border-jua-400/60">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-heading text-lg">
          <ShieldCheck className="size-5 text-jua-700" aria-hidden="true" /> {t("hospital.second.title")}
        </CardTitle>
        <CardDescription>
          {t("hospital.second.intro", { name: confirmation.confirmed_by, amount: confirmation.amount_kes_minor !== null ? formatKes(confirmation.amount_kes_minor, i18n.language) : "", invoice: confirmation.invoice_number ?? "" })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!supervisor ? (
          <Alert tone="warning">
            <AlertDescription>{t("hospital.second.needSupervisor")}</AlertDescription>
          </Alert>
        ) : sameUser ? (
          <Alert tone="warning">
            <AlertDescription>{t("hospital.second.samePerson")}</AlertDescription>
          </Alert>
        ) : (
          <>
            <OtpGate caseId={detail.id} purpose="second_approval" destinationMasked={detail.otp_destination_masked} code={otp} onCode={(value) => { setOtp(value); setOtpError(null); }} requestId={requestId} onRequest={setRequestId} error={otpError} disabled={approve.isPending} />
            <Button size="lg" disabled={!requestId || otp.length !== 6 || approve.isPending} onClick={() => approve.mutate()}>
              {approve.isPending ? t("common.loading") : t("hospital.second.approve")}
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

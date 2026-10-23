import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { isApiError, type HospitalCaseDetail } from "@/api";
import { hospitalApi, type Decision } from "@/api/hospital";
import { MoneyInput } from "@/components/MoneyInput";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, Textarea } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { toast } from "@/components/ui/toaster";
import { compareWithBill, validateDecision, type DecisionField } from "@/lib/decision";
import { formatKes, minorToDecimal } from "@/lib/money";
import { OtpGate } from "./OtpGate";
import type { Outcome } from "./OutcomeCard";

const DECISIONS: Decision[] = ["confirmed", "queried", "rejected"];

/** Confirm, query or reject the bill, with the one-time code from the hospital's official address. */
export function DecisionPanel({ detail, onDone }: { detail: HospitalCaseDetail; onDone: (outcome: Outcome) => void }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const firstBill = detail.status === "pending_confirmation";
  const [decision, setDecision] = useState<Decision>("confirmed");
  const [amount, setAmount] = useState<number | null>(null);
  const [invoice, setInvoice] = useState("");
  const [finalBill, setFinalBill] = useState(false);
  const [deposit, setDeposit] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [otp, setOtp] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [shown, setShown] = useState(false); // show field errors only after a first try
  const [serverErrors, setServerErrors] = useState<Partial<Record<DecisionField, string>>>({});
  const [otpError, setOtpError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const values = { decision, amountMinor: amount, invoice, depositMinor: deposit, note, otp, requestId };
  const clientErrors = validateDecision(values);
  const errorFor = (field: DecisionField): string | null => {
    if (serverErrors[field]) return serverErrors[field]!;
    return shown && clientErrors[field] ? t(clientErrors[field]!) : null;
  };

  const extracted = detail.triage?.extracted;
  const comparison = compareWithBill(amount, extracted?.total_kes, extracted?.balance_due_kes);
  const needsSecond = decision === "confirmed" && amount !== null && amount >= detail.two_person_threshold_kes_minor;

  const submit = useMutation({
    mutationFn: () =>
      hospitalApi.decide(detail.id, {
        requestId: requestId!,
        otp,
        decision,
        amountKes: decision === "confirmed" && amount !== null ? minorToDecimal(amount) : null,
        invoiceNumber: decision === "confirmed" ? invoice.trim().replace(/\s+/g, " ") : null,
        isFinalBill: decision === "confirmed" && (finalBill || !firstBill),
        depositKes: decision === "confirmed" && deposit !== null ? minorToDecimal(deposit) : null,
        note: decision === "confirmed" ? null : note.trim(),
      }),
    onSuccess: async (data) => {
      toast.success(t("hospital.decision.recorded"));
      onDone({ kind: data.confirmation.status === "awaiting_second_approval" ? "second_pending" : decision });
      await queryClient.invalidateQueries({ queryKey: ["hospital"] });
    },
    onError: (error) => {
      setServerErrors({});
      setOtpError(null);
      setBanner(null);
      if (isApiError(error, "otp_invalid")) setOtpError(t("hospital.otp.wrong"));
      else if (isApiError(error, "otp_expired") || isApiError(error, "otp_locked")) {
        setOtpError(isApiError(error, "otp_locked") ? t("hospital.otp.locked") : t("hospital.otp.expired"));
        setRequestId(null); // that code can no longer be used; a new one must be sent
        setOtp("");
      } else if (isApiError(error, "invoice_already_used")) setServerErrors({ invoice: t("hospital.decision.errors.invoiceUsed") });
      else if (isApiError(error, "confirmation_invalid")) {
        const next: Partial<Record<DecisionField, string>> = {};
        const fields = error.fieldErrors;
        if (fields.amount_kes) next.amount = fields.amount_kes;
        if (fields.invoice_number) next.invoice = fields.invoice_number;
        if (fields.deposit_kes) next.deposit = fields.deposit_kes;
        setServerErrors(next);
      } else if (isApiError(error, "case_not_awaiting_confirmation") || isApiError(error, "awaiting_second_approval")) {
        setBanner(t("hospital.decision.notWaiting"));
        void queryClient.invalidateQueries({ queryKey: ["hospital"] });
      } else setBanner(t("error.body"));
    },
  });

  const hint =
    decision === "confirmed" && amount !== null && comparison !== "unknown" ? (
      <p className={comparison === "matches" ? "text-sm font-semibold text-uzima-700" : "text-sm font-semibold text-jua-700"}>
        {comparison === "matches" ? t("hospital.decision.matches") : t("hospital.decision.differs", { total: extracted?.total_kes ? formatKes(Math.round(Number(extracted.total_kes) * 100), i18n.language) : "—" })}
      </p>
    ) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("hospital.decision.title")}</CardTitle>
        <CardDescription>{t("hospital.decision.intro")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          noValidate
          className="space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            setShown(true);
            setServerErrors({});
            setOtpError(null);
            if (Object.keys(clientErrors).length === 0) submit.mutate();
          }}
        >
          <fieldset className="space-y-3">
            <legend className="text-sm font-semibold">{t("hospital.decision.choose")}</legend>
            <RadioGroup value={decision} onValueChange={(value) => setDecision(value as Decision)} className="gap-2">
              {DECISIONS.map((option) => (
                <div key={option} className="flex items-start gap-3 rounded-lg border border-border p-3">
                  <RadioGroupItem value={option} id={`decision-${option}`} className="mt-0.5" />
                  <Label htmlFor={`decision-${option}`} className="space-y-0.5 font-normal">
                    <span className="block font-semibold">{t(`hospital.decision.options.${option}.label`)}</span>
                    <span className="block text-sm text-muted-foreground">{t(`hospital.decision.options.${option}.body`)}</span>
                  </Label>
                </div>
              ))}
            </RadioGroup>
          </fieldset>

          {decision === "confirmed" ? (
            <div className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="amount">{t("hospital.decision.amount")}</Label>
                <MoneyInput id="amount" currency="KES" value={amount} onChange={setAmount} invalid={Boolean(errorFor("amount"))} describedBy="amount-help" />
                <div id="amount-help" aria-live="polite" className="space-y-1">
                  <p className="text-sm text-muted-foreground">{t("hospital.decision.amountHelp")}</p>
                  {hint}
                  {errorFor("amount") ? (
                    <p role="alert" className="text-sm font-semibold text-danger-700">
                      {errorFor("amount")}
                    </p>
                  ) : null}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="invoice">{t("hospital.decision.invoice")}</Label>
                <Input id="invoice" value={invoice} maxLength={60} autoComplete="off" aria-invalid={errorFor("invoice") ? true : undefined} onChange={(event) => setInvoice(event.target.value)} />
                {errorFor("invoice") ? (
                  <p role="alert" className="text-sm font-semibold text-danger-700">
                    {errorFor("invoice")}
                  </p>
                ) : null}
              </div>

              {firstBill ? (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="deposit">{t("hospital.decision.deposit")}</Label>
                    <MoneyInput id="deposit" currency="KES" value={deposit} onChange={setDeposit} invalid={Boolean(errorFor("deposit"))} describedBy="deposit-help" />
                    <p id="deposit-help" className="text-sm text-muted-foreground">
                      {t("hospital.decision.depositHelp")}
                    </p>
                    {errorFor("deposit") ? (
                      <p role="alert" className="text-sm font-semibold text-danger-700">
                        {errorFor("deposit")}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex items-start gap-3">
                    <Checkbox id="final" checked={finalBill} onCheckedChange={(value) => setFinalBill(value === true)} />
                    <Label htmlFor="final" className="space-y-0.5 pt-0.5 font-normal">
                      <span className="block font-semibold">{t("hospital.decision.final")}</span>
                      <span className="block text-sm text-muted-foreground">{t("hospital.decision.finalHelp")}</span>
                    </Label>
                  </div>
                </>
              ) : (
                <Alert tone="info">
                  <Info />
                  <AlertDescription>{t("hospital.decision.finalNote")}</AlertDescription>
                </Alert>
              )}

              {needsSecond ? (
                <Alert tone="warning">
                  <Info />
                  <AlertDescription>{t("hospital.decision.twoPerson", { amount: formatKes(detail.two_person_threshold_kes_minor, i18n.language) })}</AlertDescription>
                </Alert>
              ) : null}
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="note">{t(decision === "queried" ? "hospital.decision.noteQuery" : "hospital.decision.noteReject")}</Label>
              <Textarea id="note" value={note} maxLength={500} aria-invalid={errorFor("note") ? true : undefined} aria-describedby="note-help" onChange={(event) => setNote(event.target.value)} />
              <p id="note-help" className="text-sm text-muted-foreground">
                {t("hospital.decision.noteHelp")}
              </p>
              {errorFor("note") ? (
                <p role="alert" className="text-sm font-semibold text-danger-700">
                  {errorFor("note")}
                </p>
              ) : null}
            </div>
          )}

          <div className="space-y-2 rounded-lg border border-border bg-tiba-50 p-4">
            <h3 className="font-heading text-base font-bold">{t("hospital.otp.title")}</h3>
            <OtpGate caseId={detail.id} purpose="decision" destinationMasked={detail.otp_destination_masked} code={otp} onCode={setOtp} requestId={requestId} onRequest={setRequestId} error={otpError ?? (shown && clientErrors.otp && requestId ? t(clientErrors.otp) : null)} disabled={submit.isPending} />
            {shown && clientErrors.otp && !requestId ? (
              <p role="alert" className="text-sm font-semibold text-danger-700">
                {t(clientErrors.otp)}
              </p>
            ) : null}
          </div>

          {banner ? (
            <Alert tone="danger">
              <AlertDescription>{banner}</AlertDescription>
            </Alert>
          ) : null}

          <Button type="submit" size="lg" disabled={submit.isPending}>
            {submit.isPending ? t("common.loading") : t(`hospital.decision.options.${decision}.submit`)}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

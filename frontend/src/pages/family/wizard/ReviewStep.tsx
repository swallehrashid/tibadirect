import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { isApiError } from "@/api";
import { CONSENT_SCOPES } from "@/api/enums";
import { familyApi, useCaseConsents, useCaseMutation, useConsentTexts, useDocuments } from "@/api/family";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toaster";
import { REQUIRED_SCOPES } from "@/lib/consents";
import type { StepProps } from "./types";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-border py-3 last:border-0 sm:flex-row sm:gap-4">
      <dt className="text-sm text-muted-foreground sm:w-48 sm:shrink-0">{label}</dt>
      <dd className="font-semibold">{children}</dd>
    </div>
  );
}

export function ReviewStep({ draft, onBack }: StepProps) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const language = i18n.language.startsWith("sw") ? "sw" : "en";
  const texts = useConsentTexts(language);
  const consents = useCaseConsents(draft?.id ?? null);
  const documents = useDocuments(draft?.id ?? null);
  const submit = useCaseMutation(async ({ caseId }: { caseId: string }) => familyApi.submit(caseId));

  if (!draft) return null;
  if (consents.isPending || documents.isPending || texts.isPending) return <Skeleton className="h-64" />;

  const active = new Set((consents.data ?? []).filter((c) => c.withdrawn_at === null).map((c) => c.scope));
  const bills = (documents.data ?? []).filter((d) => d.kind === "invoice" || d.kind === "estimate");
  const ready = REQUIRED_SCOPES.every((scope) => active.has(scope)) && bills.length > 0;
  const titleOf = (scope: string) => texts.data?.find((item) => item.scope === scope)?.title ?? scope;

  async function send() {
    try {
      await submit.mutateAsync({ caseId: draft!.id });
      toast.success(t("wizard.review.sent"));
      navigate(`/app/cases/${draft!.id}`);
    } catch {
      /* the error is shown below from submit.error */
    }
  }

  const problem = submit.error
    ? isApiError(submit.error, "consent_missing") || isApiError(submit.error, "bill_missing") || (isApiError(submit.error) && submit.error.status === 409)
      ? t("wizard.review.missing")
      : t("error.body")
    : null;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="font-heading text-xl font-bold">{t("wizard.review.title")}</h2>
        <p className="text-muted-foreground">{t("wizard.review.intro")}</p>
      </div>

      <Card>
        <CardContent>
          <dl>
            <Row label={t("wizard.hospital.label")}>{draft.hospital.display_name}</Row>
            <Row label={t("wizard.review.pageName")}>
              {draft.alias} · {draft.region}
            </Row>
            <Row label={t("wizard.hospital.patientReference")}>{draft.patient_reference}</Row>
            {draft.is_minor ? <Row label={t("wizard.hospital.minor")}>{t("common.yes")}</Row> : null}
            <Row label={t("wizard.review.bills")}>{t("wizard.review.billCount", { count: bills.length })}</Row>
            <Row label={t("wizard.review.story")}>{draft.story ? <span className="whitespace-pre-line font-normal">{draft.story}</span> : <span className="font-normal text-muted-foreground">{t("casePage.noStory")}</span>}</Row>
            <Row label={t("wizard.review.consents")}>
              <ul className="space-y-1">
                {CONSENT_SCOPES.map((scope) => (
                  <li key={scope} className="flex items-center gap-2 font-normal">
                    {active.has(scope) ? <Check className="size-4 text-uzima-700" aria-hidden="true" /> : <X className="size-4 text-muted-foreground" aria-hidden="true" />}
                    <span>
                      {titleOf(scope)}
                      <span className="sr-only">: {active.has(scope) ? t("wizard.review.given") : t("wizard.review.notGiven")}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </Row>
          </dl>
        </CardContent>
      </Card>

      <Alert tone="info">
        <AlertDescription>{t("wizard.review.whatNext")}</AlertDescription>
      </Alert>
      {!ready ? (
        <Alert tone="warning">
          <AlertDescription>{t("wizard.review.missing")}</AlertDescription>
        </Alert>
      ) : null}
      {problem ? (
        <Alert tone="danger">
          <AlertDescription>{problem}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex justify-between gap-3">
        <Button variant="outline" size="lg" onClick={onBack} disabled={submit.isPending}>
          {t("common.back")}
        </Button>
        <Button size="lg" disabled={!ready || submit.isPending} onClick={() => void send()}>
          {submit.isPending ? t("common.loading") : t("wizard.review.submit")}
        </Button>
      </div>
    </div>
  );
}

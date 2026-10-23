import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/api";
import { useHospitalCase } from "@/api/hospital";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageTitle } from "@/lib/usePageTitle";
import { BillViewer } from "./review/BillViewer";
import { ConfirmationHistory } from "./review/ConfirmationHistory";
import { DecisionPanel } from "./review/DecisionPanel";
import { ExtractedPanel } from "./review/ExtractedPanel";
import { OutcomeCard, type Outcome } from "./review/OutcomeCard";
import { SecondApprovalPanel } from "./review/SecondApprovalPanel";

export function CaseReview() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const query = useHospitalCase(id);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  usePageTitle(query.data?.alias ?? t("hospital.review.title"));

  if (query.isPending) {
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (query.isError) {
    const missing = isApiError(query.error) && query.error.status === 404;
    return missing ? (
      <EmptyState
        title={t("hospital.review.notFound.title")}
        description={t("hospital.review.notFound.body")}
        action={
          <Button asChild>
            <Link to="/hospital">{t("nav.queue")}</Link>
          </Button>
        }
      />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }

  const detail = query.data;
  const waitingSecond = detail.confirmations.find((confirmation) => confirmation.status === "awaiting_second_approval");
  const decidable = !waitingSecond && (detail.status === "pending_confirmation" || ((detail.status === "live" || detail.status === "funded") && detail.bills.some((bill) => bill.kind === "final_bill")));

  return (
    <div className="space-y-6">
      <Link to="/hospital" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-semibold text-primary underline-offset-4 hover:underline">
        <ArrowLeft className="size-4" aria-hidden="true" /> {t("nav.queue")}
      </Link>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={detail.status} />
        </div>
        <h1 className="font-heading text-3xl font-extrabold">{detail.alias}</h1>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-muted-foreground">{t("hospital.review.patientReference")}</dt>
            <dd className="font-heading text-lg font-bold">{detail.patient_reference}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">{t("hospital.review.region")}</dt>
            <dd className="font-semibold">{detail.region}</dd>
          </div>
        </dl>
        <p className="text-sm text-muted-foreground">{t("hospital.review.privacy")}</p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <BillViewer bills={detail.bills} />
        <div className="space-y-6">
          <ExtractedPanel triage={detail.triage} />
        </div>
      </div>

      {outcome ? <OutcomeCard outcome={outcome} /> : null}
      {!outcome && waitingSecond ? <SecondApprovalPanel detail={detail} confirmation={waitingSecond} onDone={setOutcome} /> : null}
      {!outcome && decidable ? <DecisionPanel detail={detail} onDone={setOutcome} /> : null}
      {!outcome && !waitingSecond && !decidable ? (
        <Alert tone="info">
          <AlertDescription>{t("hospital.review.nothingToDecide")}</AlertDescription>
        </Alert>
      ) : null}
      <ConfirmationHistory items={detail.confirmations} />
    </div>
  );
}

export const Component = CaseReview;

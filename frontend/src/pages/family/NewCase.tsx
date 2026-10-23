import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMyCase } from "@/api/family";
import { ErrorState } from "@/components/ErrorState";
import { Stepper } from "@/components/Stepper";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePageTitle } from "@/lib/usePageTitle";
import { BillStep } from "./wizard/BillStep";
import { ConsentStep } from "./wizard/ConsentStep";
import { HospitalStep } from "./wizard/HospitalStep";
import { PageStep } from "./wizard/PageStep";
import { ReviewStep } from "./wizard/ReviewStep";

const STEP_KEYS = ["hospital", "consent", "bill", "page", "review"] as const;

/**
 * The campaign wizard. The draft is saved on the server as soon as step 1 is done, so a family can
 * close the page and come back to it from their dashboard; the draft and the step live in the address.
 */
export function NewCase() {
  const { t } = useTranslation();
  usePageTitle(t("wizard.title"));
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const caseId = params.get("case");
  const requested = Number(params.get("step"));
  const draftQuery = useMyCase(caseId);

  if (caseId && draftQuery.isPending) return <Skeleton className="mx-auto h-96 max-w-2xl" />;
  if (caseId && draftQuery.isError) return <ErrorState error={draftQuery.error} onRetry={() => void draftQuery.refetch()} />;
  const draft = draftQuery.data ?? null;
  // Only a draft can be edited; anything further along lives on its dashboard.
  if (draft && draft.status !== "draft") return <Navigate to={`/app/cases/${draft.id}`} replace />;

  // Without a draft only the first step can be shown; with one, the address says which step (default: consent).
  const asked = Number.isInteger(requested) && requested >= 1 && requested <= STEP_KEYS.length ? requested - 1 : 1;
  const step = draft ? asked : 0;

  function go(index: number, id: string | null = caseId) {
    const next = new URLSearchParams();
    if (id) next.set("case", id);
    next.set("step", String(index + 1));
    setParams(next, { replace: index === 1 && !caseId });
  }

  const common = {
    draft,
    onBack: () => (step === 0 ? navigate("/app") : go(step - 1)),
    onNext: (id?: string) => go(step + 1, id ?? caseId),
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("wizard.title")}</h1>
        <p className="text-muted-foreground">{t("wizard.subtitle")}</p>
      </div>
      <Stepper steps={STEP_KEYS.map((key) => t(`wizard.steps.${key}`))} current={step} />
      <Card>
        <CardContent className="pt-5">
          {step === 0 ? <HospitalStep {...common} /> : null}
          {step === 1 ? <ConsentStep {...common} /> : null}
          {step === 2 ? <BillStep {...common} /> : null}
          {step === 3 ? <PageStep key={draft?.id} {...common} /> : null}
          {step === 4 ? <ReviewStep {...common} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}

export const Component = NewCase;

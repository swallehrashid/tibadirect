import { ArrowRight, Clock, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { QueueCase, QueueConfirmation } from "@/api";
import { useHospitalQueue } from "@/api/hospital";
import { useAuth } from "@/auth/AuthContext";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { RiskBand } from "@/components/RiskBand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatKes } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

function when(value: string | null, language: string): string {
  if (!value) return "";
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function PendingRow({ item }: { item: QueueCase }) {
  const { t, i18n } = useTranslation();
  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-4" data-testid="queue-pending">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-heading text-lg font-bold">{item.alias}</h3>
          {item.risk_band ? <RiskBand band={item.risk_band} /> : null}
          {item.kind === "final_bill" ? <Badge tone="info">{t("hospital.queue.finalBill")}</Badge> : null}
        </div>
        <p className="flex flex-wrap items-center gap-x-4 text-sm text-muted-foreground">
          <span>{item.region}</span>
          <span className="flex items-center gap-1">
            <Clock className="size-3.5" aria-hidden="true" />
            {t("hospital.queue.submitted", { when: when(item.submitted_at, i18n.language) })}
          </span>
          <span>{t("hospital.queue.bills", { count: item.bills })}</span>
        </p>
      </div>
      <Button asChild>
        <Link to={`/hospital/cases/${item.id}`}>
          {t("hospital.queue.review")} <ArrowRight />
          <span className="sr-only"> {item.alias}</span>
        </Link>
      </Button>
    </Card>
  );
}

function SecondRow({ item, canApprove }: { item: QueueConfirmation; canApprove: boolean }) {
  const { t, i18n } = useTranslation();
  return (
    <Card className="flex flex-wrap items-center justify-between gap-4 p-4" data-testid="queue-second">
      <div className="min-w-0 space-y-1">
        <h3 className="font-heading text-lg font-bold">{item.case_alias}</h3>
        <p className="text-sm text-muted-foreground">
          {item.amount_kes_minor !== null ? formatKes(item.amount_kes_minor, i18n.language) : ""}
          {item.invoice_number ? ` · ${t("hospital.queue.invoice", { number: item.invoice_number })}` : ""}
        </p>
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Users className="size-3.5" aria-hidden="true" />
          {t("hospital.queue.confirmedBy", { name: item.confirmed_by })}
        </p>
      </div>
      {canApprove ? (
        <Button asChild>
          <Link to={`/hospital/cases/${item.case_id}`}>
            {t("hospital.queue.approve")} <ArrowRight />
            <span className="sr-only"> {item.case_alias}</span>
          </Link>
        </Button>
      ) : (
        <Badge tone="pending">{t("hospital.queue.needsSupervisor")}</Badge>
      )}
    </Card>
  );
}

export function HospitalQueuePage() {
  const { t } = useTranslation();
  usePageTitle(t("nav.queue"));
  const { user } = useAuth();
  const queue = useHospitalQueue();
  const supervisor = user?.hospital?.staff_role === "billing_supervisor";

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <h1 className="font-heading text-2xl font-bold">{t("hospital.queue.title")}</h1>
        <p className="text-muted-foreground">{t("hospital.queue.intro", { hospital: user?.hospital?.display_name ?? "" })}</p>
      </div>

      {queue.isPending ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : queue.isError ? (
        <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
      ) : (
        <>
          <section aria-labelledby="waiting" className="space-y-3">
            <h2 id="waiting" className="font-heading text-xl font-bold">
              {t("hospital.queue.waiting")} <span className="text-muted-foreground">({queue.data.pending.length})</span>
            </h2>
            {queue.data.pending.length === 0 ? (
              <EmptyState title={t("hospital.queue.emptyTitle")} description={t("hospital.queue.emptyBody")} />
            ) : (
              <ul className="space-y-3">
                {queue.data.pending.map((item) => (
                  <li key={item.id}>
                    <PendingRow item={item} />
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="second" className="space-y-3">
            <div className="space-y-1">
              <h2 id="second" className="font-heading text-xl font-bold">
                {t("hospital.queue.second")} <span className="text-muted-foreground">({queue.data.awaiting_second_approval.length})</span>
              </h2>
              <p className="text-sm text-muted-foreground">{t("hospital.queue.secondIntro")}</p>
            </div>
            {queue.data.awaiting_second_approval.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("hospital.queue.secondNone")}</p>
            ) : (
              <ul className="space-y-3">
                {queue.data.awaiting_second_approval.map((item) => (
                  <li key={item.id}>
                    <SecondRow item={item} canApprove={supervisor && item.confirmed_by !== user?.display_name} />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

export const Component = HospitalQueuePage;

import { ArrowLeft, ExternalLink, Pencil } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError } from "@/api";
import { useMyCase } from "@/api/family";
import { BillMeter } from "@/components/BillMeter";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ShareButtons } from "@/components/ShareButtons";
import { StatusBadge } from "@/components/StatusBadge";
import { StatusTimeline } from "@/components/StatusTimeline";
import { TrustBadge } from "@/components/TrustBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { buildTimeline, nextActionKey } from "@/lib/caseStatus";
import { formatUsd } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";
import { ConfirmationsCard } from "./dashboard/ConfirmationsCard";
import { ConsentsCard } from "./dashboard/ConsentsCard";
import { EndCase } from "./dashboard/EndCase";
import { FinalBillCard } from "./dashboard/FinalBillCard";
import { TriageCard } from "./dashboard/TriageCard";

export function CaseDashboard() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const query = useMyCase(id);
  usePageTitle(query.data?.alias ?? t("family.dashboard.title"));

  if (query.isPending) {
    return (
      <div className="space-y-4" aria-busy="true">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-48" />
      </div>
    );
  }
  if (query.isError) {
    const missing = isApiError(query.error) && query.error.status === 404;
    return missing ? (
      <EmptyState
        title={t("family.dashboard.notFound.title")}
        description={t("family.dashboard.notFound.body")}
        action={
          <Button asChild>
            <Link to="/app">{t("nav.myCases")}</Link>
          </Button>
        }
      />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }

  const item = query.data;
  const money = item.money;
  const settling = money.pledged_kes_minor > money.settled_kes_minor;
  const timeline = buildTimeline(item.status, item.timeline, t);
  const publicUrl = `${window.location.origin}/c/${item.public_slug}`;

  return (
    <div className="space-y-6">
      <Link to="/app" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-semibold text-primary underline-offset-4 hover:underline">
        <ArrowLeft className="size-4" aria-hidden="true" /> {t("nav.myCases")}
      </Link>

      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={item.status} />
          {item.status === "draft" ? null : <TrustBadge level={item.trust_level} />}
        </div>
        <h1 className="font-heading text-3xl font-extrabold">{item.alias}</h1>
        <p className="text-muted-foreground">
          {item.hospital.display_name} · {item.region}
        </p>
        <p className="text-lg font-semibold" aria-live="polite">
          {t(nextActionKey(item.status, settling))}
        </p>
        {item.status === "draft" ? (
          <Button asChild size="lg">
            <Link to={`/app/new?case=${item.id}&step=2`}>
              <Pencil /> {t("family.continue")}
            </Link>
          </Button>
        ) : null}
      </header>

      {item.status === "frozen" ? (
        <Alert tone="warning">
          <AlertDescription>{t("family.frozenNote")}</AlertDescription>
        </Alert>
      ) : null}

      {item.is_publicly_visible ? (
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-lg">{t("family.share.title")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="break-all text-sm text-muted-foreground">{publicUrl}</p>
            <div className="flex flex-wrap items-center gap-3">
              <Button asChild variant="outline" size="sm">
                <Link to={`/c/${item.public_slug}`}>
                  <ExternalLink /> {t("family.share.view")}
                </Link>
              </Button>
              <ShareButtons title={t("share.text", { alias: item.alias })} url={publicUrl} />
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-lg">{t("family.timeline.title")}</CardTitle>
          </CardHeader>
          <CardContent>
            <StatusTimeline steps={timeline} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="font-heading text-lg">{t("family.money.title")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <BillMeter targetMinor={money.target_kes_minor} pledgedMinor={money.pledged_kes_minor} settledMinor={money.settled_kes_minor} isEstimate={money.is_estimate} usdGoalMinor={money.usd_goal_minor} />
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-muted-foreground">{t("family.money.donors")}</dt>
                <dd className="font-semibold">{money.donor_count}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("family.money.received")}</dt>
                <dd className="font-semibold">{formatUsd(money.captured_net_usd_minor)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("family.money.refunded")}</dt>
                <dd className="font-semibold">{formatUsd(money.refunded_usd_minor)}</dd>
              </div>
            </dl>
            <p className="text-sm text-muted-foreground">{t("family.money.note")}</p>
          </CardContent>
        </Card>
      </div>

      <TriageCard item={item} />
      <ConfirmationsCard item={item} />
      <FinalBillCard item={item} />
      <ConsentsCard item={item} />
      <EndCase item={item} />
    </div>
  );
}

export const Component = CaseDashboard;

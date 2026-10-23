import { ArrowLeft, Building2, MapPin } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError, usePublicCase, usePublicConfig, usePublicLedger } from "@/api";
import { BillMeter } from "@/components/BillMeter";
import { DonatePanel } from "@/components/DonatePanel";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { ShareButtons } from "@/components/ShareButtons";
import { TrancheTimeline } from "@/components/TrancheTimeline";
import { TrustBadge } from "@/components/TrustBadge";
import { VerifyLedger } from "@/components/VerifyLedger";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { tranchesFrom } from "@/lib/ledger";
import { usePageTitle } from "@/lib/usePageTitle";

// AG Grid is large, so it loads after the rest of the page is already usable.
const LedgerGrid = lazy(() => import("@/components/LedgerGrid"));

export function CasePage() {
  const { slug = "" } = useParams();
  const { t } = useTranslation();
  const { data: config } = usePublicConfig();
  const caseQuery = usePublicCase(slug);
  const ledger = usePublicLedger(slug);
  usePageTitle(caseQuery.data?.alias ?? t("casePage.title"));

  if (caseQuery.isPending) {
    return (
      <div className="container space-y-4 py-8" aria-busy="true">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (caseQuery.isError) {
    const notFound = isApiError(caseQuery.error, "case_not_found") || (isApiError(caseQuery.error) && caseQuery.error.status === 404);
    return (
      <div className="container py-10">
        {notFound ? (
          <EmptyState
            title={t("casePage.notFound.title")}
            description={t("casePage.notFound.body")}
            action={
              <Button asChild>
                <Link to="/cases">{t("landing.hero.browse")}</Link>
              </Button>
            }
          />
        ) : (
          <ErrorState error={caseQuery.error} onRetry={() => void caseQuery.refetch()} />
        )}
      </div>
    );
  }

  const item = caseQuery.data;
  const simulated = config?.paypal_mode !== "live";
  const tranches = tranchesFrom(ledger.data?.items ?? []);

  return (
    <div className="container space-y-6 py-6 md:py-10">
      <Link to="/cases" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-semibold text-primary underline-offset-4 hover:underline">
        <ArrowLeft className="size-4" aria-hidden="true" /> {t("casePage.back")}
      </Link>

      {/* One grid, one donate panel: it follows the bill meter on phones and sits beside the page on wide screens. */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-x-10 gap-y-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <header className="space-y-3 lg:col-start-1">
          <div className="flex flex-wrap items-center gap-2">
            {item.status === "funded" ? <Badge tone="confirmed">{t("caseStatus.funded")}</Badge> : null}
            {item.status === "closed" ? <Badge tone="neutral">{t("caseStatus.closed")}</Badge> : null}
          </div>
          <h1 className="font-heading text-3xl font-extrabold md:text-4xl">{item.alias}</h1>
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <MapPin className="size-4" aria-hidden="true" />
              {item.region}
            </span>
            <span className="flex items-center gap-1.5">
              <Building2 className="size-4" aria-hidden="true" />
              {item.hospital_name}
            </span>
          </p>
          <TrustBadge level={item.trust_level} explain />
          <ShareButtons title={t("share.text", { alias: item.alias })} />
          {item.status === "closed" ? (
            <Alert tone="info">
              <AlertDescription>{t("casePage.closedNote")}</AlertDescription>
            </Alert>
          ) : null}
        </header>

        <section aria-labelledby="bill" className="space-y-3 lg:col-start-1">
          <h2 id="bill" className="font-heading text-xl font-bold">
            {t("casePage.bill")}
          </h2>
          <Card>
            <CardContent>
              <BillMeter
                targetMinor={item.target_kes_minor}
                pledgedMinor={item.pledged_kes_minor}
                settledMinor={item.settled_kes_minor}
                isEstimate={item.is_estimate}
                usdGoalMinor={item.usd_goal_minor}
              />
              {item.is_estimate && item.target_kes_minor !== null ? <p className="mt-3 text-sm text-muted-foreground">{t("casePage.estimateNote")}</p> : null}
            </CardContent>
          </Card>
        </section>

        <aside aria-label={t("donate.title")} className="lg:col-start-2 lg:row-span-3 lg:row-start-1 lg:self-start lg:sticky lg:top-24">
          <DonatePanel item={item} />
        </aside>

        <div className="space-y-8 lg:col-start-1">
          <section aria-labelledby="story" className="space-y-3">
            <h2 id="story" className="font-heading text-xl font-bold">
              {t("casePage.story")}
            </h2>
            {item.story ? <p className="whitespace-pre-line text-lg leading-relaxed">{item.story}</p> : <p className="text-muted-foreground">{t("casePage.noStory")}</p>}
            <p className="text-sm text-muted-foreground">{t("casePage.noDiagnosis")}</p>
          </section>

          <section aria-labelledby="tranches" className="space-y-3">
            <h2 id="tranches" className="font-heading text-xl font-bold">
              {t("tranches.title")}
            </h2>
            {ledger.isPending ? <Skeleton className="h-16" /> : <TrancheTimeline tranches={tranches} simulated={simulated} />}
          </section>

          <section id="ledger" aria-labelledby="ledger-title" className="scroll-mt-20 space-y-3">
            <h2 id="ledger-title" className="font-heading text-xl font-bold">
              {t("ledger.title")}
            </h2>
            <p className="text-sm text-muted-foreground">{t("ledger.intro")}</p>
            <VerifyLedger slug={item.slug} />
            {ledger.isError ? (
              <ErrorState error={ledger.error} onRetry={() => void ledger.refetch()} />
            ) : ledger.isPending ? (
              <Skeleton className="h-48" />
            ) : (
              <Suspense fallback={<Skeleton className="h-48" />}>
                <LedgerGrid rows={ledger.data.items} />
              </Suspense>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

export const Component = CasePage;

import { ArrowRight, Plus } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { FamilyCaseListItem } from "@/api";
import { useMyCases } from "@/api/family";
import { useAuth } from "@/auth/AuthContext";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
import { TrustBadge } from "@/components/TrustBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { nextActionKey } from "@/lib/caseStatus";
import { formatKes } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

function CaseRow({ item }: { item: FamilyCaseListItem }) {
  const { t, i18n } = useTranslation();
  const draft = item.status === "draft";
  const settling = item.money.pledged_kes_minor > item.money.settled_kes_minor;
  const to = draft ? `/app/new?case=${item.id}&step=2` : `/app/cases/${item.id}`;
  return (
    <Card className="space-y-3 p-5" data-testid="family-case">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-heading text-lg font-bold leading-snug">{item.alias}</h2>
          <p className="text-sm text-muted-foreground">
            {item.hospital.display_name} · {item.region}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={item.status} />
          {draft ? null : <TrustBadge level={item.trust_level} />}
        </div>
      </div>
      <p className="font-semibold">{t(nextActionKey(item.status, settling))}</p>
      {item.money.target_kes_minor !== null ? (
        <p className="text-sm text-muted-foreground">
          {t("family.raised", { pledged: formatKes(item.money.pledged_kes_minor, i18n.language), target: formatKes(item.money.target_kes_minor, i18n.language) })}
        </p>
      ) : null}
      <Button asChild variant={draft ? "default" : "outline"}>
        <Link to={to}>
          {draft ? t("family.continue") : t("family.open")} <ArrowRight />
          <span className="sr-only"> {item.alias}</span>
        </Link>
      </Button>
    </Card>
  );
}

export function FamilyHome() {
  const { t } = useTranslation();
  const { user } = useAuth();
  usePageTitle(t("nav.myCases"));
  const cases = useMyCases();
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold">{t("family.title")}</h1>
          <p className="text-muted-foreground">{t("shell.welcome", { name: user?.display_name ?? "" })}</p>
        </div>
        <Button asChild size="lg">
          <Link to="/app/new">
            <Plus /> {t("nav.startCampaign")}
          </Link>
        </Button>
      </div>

      {cases.isPending ? (
        <div className="space-y-3" aria-busy="true">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      ) : cases.isError ? (
        <ErrorState error={cases.error} onRetry={() => void cases.refetch()} />
      ) : cases.data.items.length === 0 ? (
        <EmptyState
          title={t("family.empty.title")}
          description={t("family.empty.body")}
          action={
            <Button asChild size="lg">
              <Link to="/app/new">{t("nav.startCampaign")}</Link>
            </Button>
          }
        />
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {cases.data.items.map((item) => (
            <li key={item.id}>
              <CaseRow item={item} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const Component = FamilyHome;

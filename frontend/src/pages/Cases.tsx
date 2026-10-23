import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useHospitals, usePublicCases } from "@/api";
import { CaseCard, CaseCardSkeleton } from "@/components/CaseCard";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { usePageTitle } from "@/lib/usePageTitle";

const ALL = "all";

export function Cases() {
  const { t } = useTranslation();
  usePageTitle(t("cases.title"));
  const [params, setParams] = useSearchParams();
  const county = params.get("county");
  const page = Math.max(1, Number(params.get("page")) || 1);
  const cases = usePublicCases({ county, page });
  const hospitals = useHospitals();
  const counties = [...new Set((hospitals.data ?? []).map((hospital) => hospital.county))].sort((a, b) => a.localeCompare(b));
  const totalPages = cases.data ? Math.max(1, Math.ceil(cases.data.total / cases.data.page_size)) : 1;

  function update(next: { county?: string | null; page?: number }) {
    const updated = new URLSearchParams(params);
    if ("county" in next) {
      if (next.county) updated.set("county", next.county);
      else updated.delete("county");
      updated.delete("page");
    }
    if (next.page) {
      if (next.page > 1) updated.set("page", String(next.page));
      else updated.delete("page");
    }
    setParams(updated);
  }

  return (
    <div className="container space-y-6 py-8 md:py-10">
      <header className="space-y-2">
        <h1 className="font-heading text-3xl font-extrabold">{t("cases.title")}</h1>
        <p className="max-w-2xl text-muted-foreground">{t("cases.intro")}</p>
      </header>

      <div className="max-w-xs space-y-2">
        <Label htmlFor="county">{t("cases.county")}</Label>
        <Select value={county ?? ALL} onValueChange={(value) => update({ county: value === ALL ? null : value })}>
          <SelectTrigger id="county">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("cases.allCounties")}</SelectItem>
            {counties.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {cases.isError ? (
        <ErrorState error={cases.error} onRetry={() => void cases.refetch()} />
      ) : cases.isPending ? (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true">
          {Array.from({ length: 6 }, (_, index) => (
            <li key={index}>
              <CaseCardSkeleton />
            </li>
          ))}
        </ul>
      ) : cases.data.items.length === 0 ? (
        <EmptyState
          title={county ? t("cases.emptyCounty.title", { county }) : t("cases.empty.title")}
          description={county ? t("cases.emptyCounty.body") : t("cases.empty.body")}
          action={
            county ? (
              <Button variant="outline" onClick={() => update({ county: null })}>
                {t("cases.allCounties")}
              </Button>
            ) : undefined
          }
        />
      ) : (
        <>
          <p className="text-sm text-muted-foreground" aria-live="polite">
            {t("cases.count", { count: cases.data.total })}
          </p>
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {cases.data.items.map((item) => (
              <li key={item.slug}>
                <CaseCard item={item} />
              </li>
            ))}
          </ul>
          {totalPages > 1 ? (
            <nav aria-label={t("cases.pages")} className="flex items-center justify-between gap-3">
              <Button variant="outline" disabled={page <= 1} onClick={() => update({ page: page - 1 })}>
                {t("cases.previous")}
              </Button>
              <span className="text-sm text-muted-foreground">{t("cases.pageOf", { page, total: totalPages })}</span>
              <Button variant="outline" disabled={page >= totalPages} onClick={() => update({ page: page + 1 })}>
                {t("cases.next")}
              </Button>
            </nav>
          ) : null}
        </>
      )}
    </div>
  );
}

export const Component = Cases;

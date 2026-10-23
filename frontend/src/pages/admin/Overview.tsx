import type { ColDef } from "ag-grid-community";
import { AlertTriangle, ListChecks, ShieldCheck, ShieldX } from "lucide-react";
import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { AdminCaseListItem } from "@/api";
import { ADMIN_PAGE_SIZE, adminApi, useAdminCases, useAdminJobs, useAdminStats } from "@/api/admin";
import { CASE_STATUSES, isOneOf, type CaseStatus } from "@/api/enums";
import { DataGrid } from "@/components/DataGrid";
import { ErrorState } from "@/components/ErrorState";
import { StatTile } from "@/components/StatTile";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { STATUS_TONE } from "@/lib/caseStatus";
import { formatKes, formatUsd } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

const ALL = "all";

function LedgerCheck() {
  const { t } = useTranslation();
  const check = useMutation({ mutationFn: adminApi.verifyLedger });
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("admin.overview.ledger.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">{t("admin.overview.ledger.intro")}</p>
        <Button variant="outline" disabled={check.isPending} onClick={() => check.mutate()}>
          <ShieldCheck /> {check.isPending ? t("ledger.verifying") : t("admin.overview.ledger.verify")}
        </Button>
        <div aria-live="polite">
          {check.isError ? (
            <ErrorState error={check.error} onRetry={() => check.mutate()} />
          ) : check.data ? (
            check.data.valid ? (
              <Alert tone="success">
                <ShieldCheck />
                <div>
                  <AlertTitle>{t("admin.overview.ledger.ok")}</AlertTitle>
                  <AlertDescription>{t("admin.overview.ledger.okBody", { count: check.data.entries_checked })}</AlertDescription>
                </div>
              </Alert>
            ) : (
              <Alert tone="danger">
                <ShieldX />
                <div>
                  <AlertTitle>{t("admin.overview.ledger.broken")}</AlertTitle>
                  <AlertDescription>{t("admin.overview.ledger.brokenBody", { seq: check.data.first_bad_seq ?? "?" })}</AlertDescription>
                </div>
              </Alert>
            )
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function CasesTable() {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [status, setStatus] = useState<CaseStatus | null>(null);
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const cases = useAdminCases({ status, q: query }, page);
  const language = i18n.language;
  const columns = useMemo<ColDef<AdminCaseListItem>[]>(
    () => [
      { field: "alias", headerName: t("admin.cases.alias"), minWidth: 150, flex: 1, cellClass: "font-semibold" },
      { field: "status", headerName: t("admin.cases.status"), valueFormatter: (p) => t(`caseStatus.${p.value as string}`), minWidth: 190, flex: 1 },
      { field: "hospital_name", headerName: t("admin.cases.hospital"), minWidth: 200, flex: 1.3 },
      { field: "owner_email", headerName: t("admin.cases.owner"), minWidth: 220, flex: 1.3 },
      { field: "target_kes_minor", headerName: t("admin.cases.target"), valueFormatter: (p) => (p.value === null ? "—" : formatKes(p.value as number, language)), minWidth: 150, type: "rightAligned" },
      { field: "pledged_kes_minor", headerName: t("admin.cases.pledged"), valueFormatter: (p) => formatKes(p.value as number, language), minWidth: 150, type: "rightAligned" },
      { field: "settled_kes_minor", headerName: t("admin.cases.settled"), valueFormatter: (p) => formatKes(p.value as number, language), minWidth: 150, type: "rightAligned" },
      {
        colId: "open",
        headerName: "",
        sortable: false,
        pinned: "right",
        resizable: false,
        minWidth: 110,
        maxWidth: 130,
        cellRenderer: (p: { data?: AdminCaseListItem }) =>
          p.data ? (
            <Button asChild variant="outline" size="sm">
              <Link to={`/admin/cases/${p.data.id}`}>
                {t("admin.cases.open")}
                <span className="sr-only">: {p.data.alias}</span>
              </Link>
            </Button>
          ) : null,
      },
    ],
    [t, language],
  );
  const totalPages = cases.data ? Math.max(1, Math.ceil(cases.data.total / ADMIN_PAGE_SIZE)) : 1;

  return (
    <section aria-labelledby="all-cases" className="space-y-3">
      <h2 id="all-cases" className="font-heading text-xl font-bold">
        {t("admin.cases.title")}
      </h2>
      <form
        className="flex flex-wrap items-end gap-3"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(text.trim());
          setPage(1);
        }}
      >
        <div className="min-w-48 flex-1 space-y-2">
          <Label htmlFor="case-search">{t("admin.cases.search")}</Label>
          <Input id="case-search" type="search" value={text} onChange={(event) => setText(event.target.value)} />
        </div>
        <div className="w-60 space-y-2">
          <Label htmlFor="case-status">{t("admin.cases.status")}</Label>
          <Select
            value={status ?? ALL}
            onValueChange={(value) => {
              setStatus(isOneOf(CASE_STATUSES, value) ? value : null);
              setPage(1);
            }}
          >
            <SelectTrigger id="case-status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("admin.cases.allStatuses")}</SelectItem>
              {CASE_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`caseStatus.${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button type="submit" variant="outline">
          {t("admin.cases.searchButton")}
        </Button>
      </form>
      {cases.isError ? (
        <ErrorState error={cases.error} onRetry={() => void cases.refetch()} />
      ) : cases.isPending ? (
        <Skeleton className="h-48" />
      ) : (
        <>
          <DataGrid rows={cases.data.items} columns={columns} getRowId={(row) => row.id} label={t("admin.cases.title")} noRows={t("admin.cases.none")} onRowClicked={(row) => navigate(`/admin/cases/${row.id}`)} />
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {t("admin.cases.count", { count: cases.data.total })}
            </p>
            {totalPages > 1 ? (
              <div className="flex items-center gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                  {t("cases.previous")}
                </Button>
                <span className="text-sm text-muted-foreground">{t("cases.pageOf", { page, total: totalPages })}</span>
                <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                  {t("cases.next")}
                </Button>
              </div>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">{t("admin.cases.hint")}</p>
        </>
      )}
    </section>
  );
}

export function AdminOverview() {
  const { t, i18n } = useTranslation();
  usePageTitle(t("nav.overview"));
  const stats = useAdminStats();
  const jobs = useAdminJobs();
  const language = i18n.language;

  return (
    <div className="space-y-8">
      <h1 className="font-heading text-2xl font-bold">{t("admin.overview.title")}</h1>

      {stats.isPending ? (
        <Skeleton className="h-40" />
      ) : stats.isError ? (
        <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
      ) : (
        <>
          <section aria-labelledby="waiting" className="space-y-3">
            <h2 id="waiting" className="font-heading text-xl font-bold">
              {t("admin.overview.waiting")}
            </h2>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile to="/admin/review" label={t("admin.overview.openReviews")} value={stats.data.waiting.open_review_items} tone={stats.data.waiting.open_review_items > 0 ? "attention" : undefined} hint={t("admin.overview.openReviewsHint")} />
              <StatTile label={t("admin.overview.secondApprovals")} value={stats.data.waiting.awaiting_second_approval} hint={t("admin.overview.secondApprovalsHint")} />
              <StatTile to="/admin/jobs" label={t("admin.overview.failedJobs")} value={jobs.data ? jobs.data.failed_jobs.length : "…"} tone={jobs.data && jobs.data.failed_jobs.length > 0 ? "attention" : undefined} hint={jobs.data ? (jobs.data.scheduler.healthy ? t("admin.jobs.schedulerOk") : t("admin.jobs.schedulerDown")) : undefined} />
              <StatTile label={t("admin.overview.ledgerEntries")} value={stats.data.ledger_entries} />
            </dl>
          </section>

          <section aria-labelledby="money" className="space-y-3">
            <h2 id="money" className="font-heading text-xl font-bold">
              {t("admin.overview.money")}
            </h2>
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <StatTile label={t("admin.overview.donations")} value={stats.data.money.donations} />
              <StatTile label={t("admin.overview.captured")} value={formatUsd(stats.data.money.captured_net_usd_minor)} hint={t("admin.overview.capturedHint")} />
              <StatTile label={t("admin.overview.refunded")} value={formatUsd(stats.data.money.refunded_usd_minor)} />
              <StatTile label={t("admin.overview.settledUsd")} value={formatUsd(stats.data.money.settled_usd_minor)} />
              <StatTile label={t("admin.overview.pledged")} value={formatKes(stats.data.money.pledged_kes_minor, language)} />
              <StatTile label={t("admin.overview.settledKes")} value={formatKes(stats.data.money.settled_kes_minor, language)} />
            </dl>
          </section>

          <section aria-labelledby="by-status" className="space-y-3">
            <h2 id="by-status" className="font-heading text-xl font-bold">
              {t("admin.overview.cases", { count: stats.data.cases_total })}
            </h2>
            <ul className="flex flex-wrap gap-2">
              {CASE_STATUSES.filter((status) => stats.data.cases_by_status[status]).map((status) => (
                <li key={status}>
                  <Badge tone={STATUS_TONE[status]}>
                    {t(`caseStatus.${status}`)}: {stats.data.cases_by_status[status]}
                  </Badge>
                </li>
              ))}
            </ul>
            {stats.data.queues === null ? (
              <Alert tone="warning">
                <AlertTriangle />
                <AlertDescription>{t("admin.overview.queuesDown")}</AlertDescription>
              </Alert>
            ) : (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <ListChecks className="size-4" aria-hidden="true" />
                {t("admin.overview.queueLine", { queues: stats.data.queues.map((queue) => `${queue.name}: ${queue.queued}`).join(" · ") })}
              </p>
            )}
          </section>
        </>
      )}

      <LedgerCheck />
      <CasesTable />
    </div>
  );
}

export const Component = AdminOverview;

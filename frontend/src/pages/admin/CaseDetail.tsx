import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Snowflake } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError, type AdminCase, type SettlementRun } from "@/api";
import { adminApi, useAdminCase } from "@/api/admin";
import { BillMeter } from "@/components/BillMeter";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { RiskBand } from "@/components/RiskBand";
import { StatusBadge } from "@/components/StatusBadge";
import { TrustBadge } from "@/components/TrustBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/input";
import { toast } from "@/components/ui/toaster";
import { formatKes, formatUsd } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";
import { ConfirmationHistory } from "../hospital/review/ConfirmationHistory";
import { ExtractedPanel } from "../hospital/review/ExtractedPanel";

const FREEZABLE = new Set(["pending_triage", "in_review", "pending_confirmation", "live", "funded"]);

function Section({ title, children, empty }: { title: string; children?: ReactNode; empty?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{title}</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto">{children ?? <p className="text-sm text-muted-foreground">{empty}</p>}</CardContent>
    </Card>
  );
}

function Table({ columns, rows, label }: { columns: string[]; rows: ReactNode[][]; label: string }) {
  return (
    <table className="w-full min-w-[32rem] text-sm">
      <caption className="sr-only">{label}</caption>
      <thead>
        <tr className="border-b border-border text-left text-muted-foreground">
          {columns.map((column) => (
            <th key={column} scope="col" className="px-2 py-2 font-semibold first:pl-0">
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={index} className="border-b border-border align-top last:border-0">
            {row.map((cell, position) => (
              <td key={position} className="px-2 py-2 first:pl-0">
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FreezeCase({ item }: { item: AdminCase }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [open, setOpen] = useState(false);
  const freeze = useMutation({
    mutationFn: () => adminApi.freeze(item.id, reason.trim()),
    onSuccess: async (updated) => {
      queryClient.setQueryData(["admin", "case", item.id], updated);
      toast.success(t("admin.case.freeze.done"));
      setOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["admin"], predicate: (query) => query.queryKey[1] !== "case" });
    },
  });
  if (!FREEZABLE.has(item.status)) return null;
  const valid = reason.trim().length >= 10 && reason.trim().length <= 300;
  const problem = freeze.error ? (isApiError(freeze.error) && freeze.error.status === 409 ? t("admin.case.freeze.conflict") : t("error.body")) : null;
  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="destructive">
          <Snowflake /> {t("admin.case.freeze.button")}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("admin.case.freeze.title", { alias: item.alias })}</AlertDialogTitle>
          <AlertDialogDescription>{t("admin.case.freeze.warning")}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2">
          <Label htmlFor="freeze-reason">{t("admin.case.freeze.reason")}</Label>
          <Textarea id="freeze-reason" value={reason} maxLength={300} onChange={(event) => setReason(event.target.value)} aria-describedby="freeze-help" />
          <p id="freeze-help" className="text-sm text-muted-foreground">
            {t("admin.case.freeze.help", { count: reason.trim().length })}
          </p>
        </div>
        {problem ? (
          <Alert tone="danger">
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          {/* A plain button, not AlertDialogAction: the dialog must stay open if the freeze is refused. */}
          <Button variant="destructive" disabled={!valid || freeze.isPending} onClick={() => freeze.mutate()}>
            {freeze.isPending ? t("common.loading") : t("admin.case.freeze.confirm")}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SettlementCheck({ item }: { item: AdminCase }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const run = useMutation({
    mutationFn: () => adminApi.runSettlement(item.id),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["admin"], predicate: (query) => query.queryKey[1] !== "case" }),
  });
  const result: SettlementRun | undefined = run.data;
  return (
    <div className="space-y-2">
      <Button variant="outline" disabled={run.isPending} onClick={() => run.mutate()}>
        {run.isPending ? t("common.loading") : t("admin.case.settle.button")}
      </Button>
      <div aria-live="polite">
        {run.isError ? (
          <Alert tone="danger">
            <AlertDescription>{t("error.body")}</AlertDescription>
          </Alert>
        ) : result ? (
          <Alert tone={result.reason === "created" ? "success" : "info"}>
            <AlertDescription>
              {result.reason === "created"
                ? t("admin.case.settle.created", { n: result.tranche_no, amount: formatKes(result.amount_kes_minor ?? 0, i18n.language) })
                : t(`admin.case.settle.reasons.${result.reason}`, { defaultValue: result.reason })}
            </AlertDescription>
          </Alert>
        ) : null}
      </div>
    </div>
  );
}

export function AdminCaseDetail() {
  const { id = "" } = useParams();
  const { t, i18n } = useTranslation();
  const query = useAdminCase(id);
  usePageTitle(query.data?.alias ?? t("admin.case.title"));
  const language = i18n.language;
  const date = (value: string | null) => (value ? new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—");

  if (query.isPending) return <Skeleton className="h-96" />;
  if (query.isError) {
    return isApiError(query.error) && query.error.status === 404 ? (
      <EmptyState
        title={t("admin.case.notFound")}
        action={
          <Button asChild>
            <Link to="/admin">{t("nav.overview")}</Link>
          </Button>
        }
      />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  }
  const item = query.data;

  return (
    <div className="space-y-6">
      <Link to="/admin" className="inline-flex min-h-10 items-center gap-1.5 text-sm font-semibold text-primary underline-offset-4 hover:underline">
        <ArrowLeft className="size-4" aria-hidden="true" /> {t("nav.overview")}
      </Link>

      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={item.status} />
          <TrustBadge level={item.trust_level} />
          {item.close_reason ? <Badge tone="neutral">{item.close_reason}</Badge> : null}
        </div>
        <h1 className="font-heading text-3xl font-extrabold">{item.alias}</h1>
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
          {(
            [
              [t("admin.case.hospital"), item.hospital.display_name],
              [t("admin.case.owner"), `${item.owner.display_name} · ${item.owner.email}`],
              [t("hospital.review.region"), item.region],
              [t("hospital.review.patientReference"), item.patient_reference],
              [t("admin.case.minor"), item.is_minor ? t("common.yes") : t("common.no")],
              [t("admin.case.slug"), item.public_slug],
              [t("admin.case.created"), date(item.created_at)],
              [t("admin.case.live"), date(item.live_at)],
              [t("admin.case.closed"), date(item.closed_at)],
            ] as const
          ).map(([label, value]) => (
            <div key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="break-words font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
        {item.freeze_reason ? (
          <Alert tone="warning">
            <AlertDescription>{t("admin.case.freezeReason", { reason: item.freeze_reason, when: date(item.frozen_at) })}</AlertDescription>
          </Alert>
        ) : null}
        {item.story ? <p className="max-w-3xl whitespace-pre-line text-muted-foreground">{item.story}</p> : null}
        <p className="text-xs text-muted-foreground">{t("admin.case.audited")}</p>
        <div className="flex flex-wrap gap-3">
          <FreezeCase item={item} />
          <SettlementCheck item={item} />
        </div>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
        <Section title={t("family.money.title")}>
          <div className="space-y-4">
            <BillMeter targetMinor={item.money.target_kes_minor} pledgedMinor={item.money.pledged_kes_minor} settledMinor={item.money.settled_kes_minor} isEstimate={item.money.is_estimate} usdGoalMinor={item.money.usd_goal_minor} />
            <dl className="grid grid-cols-3 gap-3 text-sm">
              <div>
                <dt className="text-muted-foreground">{t("family.money.donors")}</dt>
                <dd className="font-semibold">{item.money.donor_count}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("family.money.received")}</dt>
                <dd className="font-semibold">{formatUsd(item.money.captured_net_usd_minor)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("family.money.refunded")}</dt>
                <dd className="font-semibold">{formatUsd(item.money.refunded_usd_minor)}</dd>
              </div>
            </dl>
          </div>
        </Section>

        <Section title={t("admin.case.documents")} empty={t("admin.case.noDocuments")}>
          {item.documents.length > 0 ? (
            <Table
              label={t("admin.case.documents")}
              columns={[t("admin.case.kind"), t("admin.review.columns.status"), t("admin.case.size"), t("admin.case.pages")]}
              rows={item.documents.map((doc) => [t(`hospital.bill.kinds.${doc.kind}`), doc.status, `${(doc.size_bytes / 1024).toFixed(0)} kB`, doc.page_count ?? "—"])}
            />
          ) : undefined}
        </Section>
      </div>

      <Section title={t("admin.case.checks")} empty={t("admin.case.noChecks")}>
        {item.triage.length > 0 ? (
          <div className="space-y-6">
            {item.triage.map((result) => (
              <div key={result.document_id + result.created_at} className="space-y-3 rounded-lg border border-border p-4">
                <div className="flex flex-wrap items-center gap-3">
                  <RiskBand band={result.risk_band} />
                  <span className="text-sm">{t("admin.case.score", { score: result.risk_score })}</span>
                  <Badge tone="neutral">{t(`family.triage.route.${result.route}`)}</Badge>
                  <span className="text-sm text-muted-foreground">{result.used_llm ? t("admin.case.usedModel") : t("admin.case.localOnly")}</span>
                  <span className="text-sm text-muted-foreground">{date(result.created_at)}</span>
                </div>
                {result.signals.length > 0 ? (
                  <ul className="space-y-1">
                    {result.signals.map((signal, position) => (
                      <li key={position} className="rounded-md bg-muted p-2 font-mono text-xs">
                        {JSON.stringify(signal)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground">{t("admin.case.noSignals")}</p>
                )}
                <ExtractedPanel triage={{ risk_band: result.risk_band, reasons_en: result.reasons_en, reasons_sw: result.reasons_en, extracted: result.extracted, created_at: result.created_at }} />
              </div>
            ))}
          </div>
        ) : undefined}
      </Section>

      <ConfirmationHistory items={item.confirmations} />

      <Section title={t("admin.case.donations", { count: item.donations.length })} empty={t("admin.case.noDonations")}>
        {item.donations.length > 0 ? (
          <Table
            label={t("admin.case.donations", { count: item.donations.length })}
            columns={[t("admin.case.paid"), t("admin.review.columns.status"), t("admin.case.amount"), t("receipt.fee"), t("receipt.net"), t("admin.case.refundedColumn"), t("receipt.excess"), t("admin.case.donor")]}
            rows={item.donations.map((d) => [date(d.captured_at), d.status, formatUsd(d.amount_usd_minor), d.paypal_fee_usd_minor === null ? "—" : formatUsd(d.paypal_fee_usd_minor), formatUsd(d.net_usd_minor), formatUsd(d.refunded_usd_minor), d.excess_choice ? t(`donate.excess.${d.excess_choice}.label`) : "—", d.is_anonymous ? t("admin.case.anonymous") : (d.donor_display_name ?? "—")])}
          />
        ) : undefined}
      </Section>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
        <Section title={t("admin.case.settlements")} empty={t("admin.case.noSettlements")}>
          {item.settlements.length > 0 ? (
            <Table
              label={t("admin.case.settlements")}
              columns={[t("hospital.settlements.tranche"), t("admin.review.columns.status"), "USD", "KES", t("admin.case.adapter")]}
              rows={item.settlements.map((s) => [s.tranche_no, s.failure_reason ? `${s.status}: ${s.failure_reason}` : s.status, formatUsd(s.amount_usd_minor), formatKes(s.amount_kes_minor, language), s.adapter])}
            />
          ) : undefined}
        </Section>
        <Section title={t("admin.case.refunds")} empty={t("admin.case.noRefunds")}>
          {item.refunds.length > 0 ? (
            <Table label={t("admin.case.refunds")} columns={[t("admin.case.amount"), t("admin.case.reason"), t("admin.review.columns.status"), t("admin.case.by")]} rows={item.refunds.map((r) => [formatUsd(r.amount_usd_minor), r.reason, r.status, r.requested_by])} />
          ) : undefined}
        </Section>
      </div>

      <Section title={t("family.consents.title")} empty={t("family.consents.none")}>
        {item.consents.length > 0 ? (
          <Table
            label={t("family.consents.title")}
            columns={[t("admin.case.scope"), t("admin.case.by"), t("admin.case.version"), t("admin.case.granted"), t("admin.case.withdrawn")]}
            rows={item.consents.map((c) => [c.scope, c.actor, `${c.wording_version} (${c.language})`, date(c.granted_at), date(c.withdrawn_at)])}
          />
        ) : undefined}
      </Section>

      <Section title={t("admin.case.reviews")} empty={t("admin.case.noReviews")}>
        {item.review_items.length > 0 ? (
          <ul className="space-y-2">
            {item.review_items.map((review) => (
              <li key={review.id} className="rounded-lg border border-border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={review.status === "open" ? "pending" : "neutral"}>{t(`admin.review.status.${review.status}`)}</Badge>
                  <span className="font-semibold">{t(`admin.review.sources.${review.source}`)}</span>
                  <span className="text-muted-foreground">{date(review.created_at)}</span>
                </div>
                <p>{review.reason}</p>
                {review.resolution ? (
                  <p className="text-muted-foreground">
                    {t(`admin.review.resolutions.${review.resolution}.label`)}
                    {review.resolution_note ? `: ${review.resolution_note}` : ""}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : undefined}
      </Section>

      <Section title={t("admin.case.trail", { count: item.audit_trail.length })} empty={t("admin.audit.emptyBody")}>
        {item.audit_trail.length > 0 ? (
          <Table
            label={t("admin.case.trail", { count: item.audit_trail.length })}
            columns={[t("admin.audit.columns.when"), t("admin.audit.columns.actor"), t("admin.audit.columns.action"), t("admin.audit.columns.details")]}
            rows={item.audit_trail.map((e) => [date(e.created_at), e.actor_type, <span key="a" className="font-mono text-xs font-semibold">{e.action}</span>, <span key="d" className="break-all font-mono text-xs">{JSON.stringify(e.details)}</span>])}
          />
        ) : undefined}
      </Section>
    </div>
  );
}

export const Component = AdminCaseDetail;

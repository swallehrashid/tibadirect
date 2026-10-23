import { CheckCircle2, Printer } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { isApiError, useReceipt } from "@/api";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SandboxTag } from "@/components/SandboxTag";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatKes, formatUsd } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border py-2.5 last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={strong ? "text-right font-heading text-lg font-bold" : "text-right font-semibold"}>{value}</dd>
    </div>
  );
}

export function ReceiptPage() {
  const { token = "" } = useParams();
  const { t, i18n } = useTranslation();
  usePageTitle(t("receipt.title"));
  const receipt = useReceipt(token);

  if (receipt.isPending) {
    return (
      <div className="container max-w-2xl py-10" aria-busy="true">
        <Skeleton className="h-96" />
      </div>
    );
  }
  if (receipt.isError) {
    const missing = isApiError(receipt.error) && receipt.error.status === 404;
    return (
      <div className="container max-w-2xl py-10">
        {missing ? (
          <EmptyState title={t("receipt.notFound.title")} description={t("receipt.notFound.body")} action={<Button asChild><Link to="/cases">{t("landing.hero.browse")}</Link></Button>} />
        ) : (
          <ErrorState error={receipt.error} onRetry={() => void receipt.refetch()} />
        )}
      </div>
    );
  }

  const r = receipt.data;
  const when = r.captured_at ? new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { dateStyle: "long", timeStyle: "short" }).format(new Date(r.captured_at)) : null;
  const refunded = r.status === "refunded" || r.status === "partially_refunded";
  return (
    <div className="container max-w-2xl space-y-6 py-8 md:py-12">
      <div className="space-y-2 text-center">
        <CheckCircle2 className="mx-auto size-12 text-uzima-700" aria-hidden="true" />
        <h1 className="font-heading text-3xl font-extrabold">{t("receipt.thanks")}</h1>
        <p className="text-muted-foreground">{t("receipt.lead", { amount: formatUsd(r.amount_usd_minor), alias: r.case_alias, hospital: r.hospital_name })}</p>
        <SandboxTag />
      </div>

      {refunded ? (
        <Alert tone="info">
          <AlertDescription>{r.status === "refunded" ? t("receipt.refunded") : t("receipt.partiallyRefunded", { amount: formatUsd(r.refunded_usd_minor) })}</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">{t("receipt.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <dl>
            <Row label={t("receipt.amount")} value={formatUsd(r.amount_usd_minor)} strong />
            <Row label={t("receipt.fee")} value={formatUsd(r.paypal_fee_usd_minor)} />
            <Row label={t("receipt.net")} value={formatUsd(r.net_usd_minor)} />
            <Row label={t("receipt.kes")} value={`${formatKes(r.kes_estimate_minor, i18n.language)}${r.fx_rate ? ` (1 USD = ${Number(r.fx_rate).toFixed(2)} KES)` : ""}`} />
            <Row label={t("receipt.excess")} value={t(`donate.excess.${r.excess_choice}.label`)} />
            {r.donor_display_name ? <Row label={t("receipt.name")} value={r.donor_display_name} /> : null}
            {when ? <Row label={t("receipt.date")} value={when} /> : null}
            {r.ledger_reference ? <Row label={t("receipt.reference")} value={r.ledger_reference} /> : null}
            {r.ledger_hash_short ? <Row label={t("receipt.fingerprint")} value={r.ledger_hash_short} /> : null}
          </dl>
          <p className="mt-4 text-sm text-muted-foreground">{t("receipt.kesNote")}</p>
        </CardContent>
      </Card>

      <div className="flex flex-wrap justify-center gap-3 print:hidden">
        <Button asChild size="lg">
          <Link to={`/c/${r.case_slug}#ledger`}>{t("receipt.seeLedger")}</Link>
        </Button>
        <Button variant="outline" size="lg" onClick={() => window.print()}>
          <Printer /> {t("receipt.print")}
        </Button>
      </div>
      <p className="text-center text-sm text-muted-foreground print:hidden">{t("receipt.keepLink")}</p>
    </div>
  );
}

export const Component = ReceiptPage;

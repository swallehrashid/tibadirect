import { useTranslation } from "react-i18next";
import type { HospitalTriage } from "@/api";
import { RiskBand } from "@/components/RiskBand";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatKes, parseMinor } from "@/lib/money";

function amount(value: string | null | undefined, language: string): string {
  const minor = value ? parseMinor(value) : null;
  return minor === null ? "—" : formatKes(minor, language);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border py-2 text-sm last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-semibold">{children}</dd>
    </div>
  );
}

/** What our software read from the bill and what it noticed, to compare with the original. It is a helper, not the answer. */
export function ExtractedPanel({ triage }: { triage: HospitalTriage | null }) {
  const { t, i18n } = useTranslation();
  if (!triage) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-lg">{t("hospital.extracted.title")}</CardTitle>
          <CardDescription>{t("hospital.extracted.none")}</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const x = triage.extracted;
  const reasons = i18n.language.startsWith("sw") ? triage.reasons_sw : triage.reasons_en;
  const lines = x.line_items ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("hospital.extracted.title")}</CardTitle>
        <CardDescription>{t("hospital.extracted.intro")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl>
          <Field label={t("hospital.extracted.facility")}>{x.facility_name ?? "—"}</Field>
          <Field label={t("hospital.extracted.invoice")}>{x.invoice_number ?? "—"}</Field>
          <Field label={t("hospital.extracted.date")}>{x.invoice_date ?? "—"}</Field>
          <Field label={t("hospital.extracted.type")}>{x.document_type && x.document_type !== "unknown" ? t(`hospital.bill.kinds.${x.document_type}`) : "—"}</Field>
          <Field label={t("hospital.extracted.total")}>{amount(x.total_kes, i18n.language)}</Field>
          <Field label={t("hospital.extracted.balance")}>{amount(x.balance_due_kes, i18n.language)}</Field>
          <Field label={t("hospital.extracted.deposit")}>{amount(x.deposit_kes, i18n.language)}</Field>
          <Field label={t("hospital.extracted.confidence")}>{x.confidence === undefined ? "—" : `${Math.round(x.confidence * 100)}%`}</Field>
        </dl>

        {lines.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="pb-2 text-left font-semibold">{t("hospital.extracted.lines")}</caption>
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th scope="col" className="py-1.5 pr-3 font-semibold">
                    {t("hospital.extracted.item")}
                  </th>
                  <th scope="col" className="px-3 py-1.5 text-right font-semibold">
                    {t("hospital.extracted.qty")}
                  </th>
                  <th scope="col" className="py-1.5 pl-3 text-right font-semibold">
                    {t("hospital.extracted.lineAmount")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {lines.map((line, position) => (
                  <tr key={`${line.label}-${position}`} className="border-b border-border last:border-0">
                    <td className="py-1.5 pr-3">{line.label}</td>
                    <td className="px-3 py-1.5 text-right">{line.quantity ?? ""}</td>
                    <td className="py-1.5 pl-3 text-right font-semibold">{amount(line.amount_kes, i18n.language)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-heading text-base font-bold">{t("hospital.extracted.checks")}</h3>
            <RiskBand band={triage.risk_band} />
          </div>
          {reasons.length > 0 ? (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">{t("family.triage.noReasons")}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

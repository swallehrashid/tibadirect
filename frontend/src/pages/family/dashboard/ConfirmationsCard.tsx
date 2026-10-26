import { useTranslation } from "react-i18next";
import type { FamilyCase } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatKes } from "@/lib/money";

const TONE = { confirmed: "confirmed", awaiting_second_approval: "pending", queried: "pending", rejected: "danger" } as const;

/** What the hospital has said about the bill: confirmed, a question for the family, or a refusal. */
export function ConfirmationsCard({ item }: { item: FamilyCase }) {
  const { t, i18n } = useTranslation();
  if (item.confirmations.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("family.confirmations.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-3">
          {item.confirmations.map((confirmation) => (
            <li key={confirmation.created_at} className="space-y-1 rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[confirmation.status]}>{t(`family.confirmations.status.${confirmation.status}`)}</Badge>
                {confirmation.is_final_bill ? <Badge tone="info">{t("family.confirmations.final")}</Badge> : null}
              </div>
              {confirmation.amount_kes_minor !== null ? <p className="font-semibold">{formatKes(confirmation.amount_kes_minor, i18n.language)}</p> : null}
              {confirmation.invoice_number ? <p className="text-sm text-muted-foreground">{t("family.confirmations.invoice", { number: confirmation.invoice_number })}</p> : null}
              {confirmation.query_note ? (
                <p className="text-sm">
                  <span className="font-semibold">{t("family.confirmations.note")}</span> {confirmation.query_note}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

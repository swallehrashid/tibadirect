import { useTranslation } from "react-i18next";
import type { HospitalConfirmation } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatKes } from "@/lib/money";

const TONE = { confirmed: "confirmed", awaiting_second_approval: "pending", queried: "pending", rejected: "danger" } as const;

/** Earlier decisions on this case, newest first, with who made them and the PayPal invoice status. */
export function ConfirmationHistory({ items }: { items: HospitalConfirmation[] }) {
  const { t, i18n } = useTranslation();
  if (items.length === 0) return null;
  const date = (value: string) => new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("hospital.history.title")}</CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.id} className="space-y-1 rounded-lg border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={TONE[item.status]}>{t(`family.confirmations.status.${item.status}`)}</Badge>
                {item.is_final_bill ? <Badge tone="info">{t("family.confirmations.final")}</Badge> : null}
                {item.invoice_status ? <Badge tone={item.invoice_status === "FAILED" ? "danger" : "neutral"}>{t("hospital.history.invoiceStatus", { status: item.invoice_status })}</Badge> : null}
              </div>
              {item.amount_kes_minor !== null ? <p className="font-semibold">{formatKes(item.amount_kes_minor, i18n.language)}</p> : null}
              {item.invoice_number ? <p className="text-sm text-muted-foreground">{t("family.confirmations.invoice", { number: item.invoice_number })}</p> : null}
              <p className="text-sm text-muted-foreground">
                {t("hospital.history.by", { name: item.confirmed_by, when: date(item.created_at) })}
                {item.second_approver ? ` · ${t("hospital.history.second", { name: item.second_approver })}` : ""}
              </p>
              {item.query_note ? (
                <p className="text-sm">
                  <span className="font-semibold">{t("hospital.history.note")}</span> {item.query_note}
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

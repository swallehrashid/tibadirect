import { ExternalLink, FileX } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { HospitalBill } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** The original bill, from a link that works for five minutes. The page fetches fresh links before they run out. */
export function BillViewer({ bills }: { bills: HospitalBill[] }) {
  const { t, i18n } = useTranslation();
  const [index, setIndex] = useState(0);
  const bill = bills[Math.min(index, bills.length - 1)];
  const date = (value: string) => new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short" }).format(new Date(value));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-heading text-lg">{t("hospital.bill.title")}</CardTitle>
        {bills.length > 1 ? (
          <div role="tablist" aria-label={t("hospital.bill.which")} className="flex flex-wrap gap-2 pt-2">
            {bills.map((entry, position) => (
              <button
                key={entry.id}
                role="tab"
                type="button"
                aria-selected={position === index}
                onClick={() => setIndex(position)}
                className={cn("min-h-10 rounded-md border px-3 text-sm font-semibold", position === index ? "border-primary bg-primary text-primary-foreground" : "border-border bg-card")}
              >
                {t(`hospital.bill.kinds.${entry.kind}`)} · {date(entry.created_at)}
              </button>
            ))}
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {!bill ? (
          <p className="text-sm text-muted-foreground">{t("hospital.bill.none")}</p>
        ) : bill.preview_url ? (
          <>
            {bill.mime_type.startsWith("image/") ? (
              <img src={bill.preview_url} alt={t("hospital.bill.imageAlt")} className="max-h-[70vh] w-full rounded-md border border-border object-contain" referrerPolicy="no-referrer" />
            ) : (
              <iframe src={bill.preview_url} title={t("hospital.bill.pdfTitle")} className="h-[70vh] w-full rounded-md border border-border" referrerPolicy="no-referrer" />
            )}
            <Button asChild variant="outline" size="sm">
              <a href={bill.preview_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink /> {t("hospital.bill.open")}
                <span className="sr-only"> ({t("share.opensNewTab")})</span>
              </a>
            </Button>
          </>
        ) : (
          <div className="flex items-start gap-3 rounded-lg border border-dashed border-input p-4 text-sm text-muted-foreground">
            <FileX className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
            <p>{t("hospital.bill.gone")}</p>
          </div>
        )}
        <p className="text-sm text-muted-foreground">{t("hospital.bill.hint")}</p>
      </CardContent>
    </Card>
  );
}

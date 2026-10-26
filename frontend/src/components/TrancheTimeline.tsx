import { Building2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SandboxTag } from "@/components/SandboxTag";
import { formatKes } from "@/lib/money";
import type { Tranche } from "@/lib/ledger";

/** The payments made to the hospital so far, oldest first. */
export function TrancheTimeline({ tranches, simulated }: { tranches: Tranche[]; simulated: boolean }) {
  const { t, i18n } = useTranslation();
  const date = (value: string) => new Intl.DateTimeFormat(i18n.language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", year: "numeric" }).format(new Date(value));
  return (
    <div className="space-y-3">
      {tranches.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("tranches.none")}</p>
      ) : (
        <ol className="space-y-3">
          {tranches.map((tranche, index) => (
            <li key={`${tranche.at}-${index}`} className="flex items-start gap-3 rounded-lg border border-border bg-card p-3">
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-uzima-100 text-uzima-700" aria-hidden="true">
                <Building2 className="size-4" />
              </span>
              <div>
                <p className="font-semibold">{t("tranches.item", { n: index + 1, amount: formatKes(tranche.amountKesMinor, i18n.language) })}</p>
                <p className="text-sm text-muted-foreground">
                  {date(tranche.at)}
                  {tranche.reference ? ` · ${tranche.reference}` : ""}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
      {simulated ? (
        <div className="flex flex-wrap items-start gap-2 text-sm text-muted-foreground">
          <SandboxTag />
          <p className="min-w-0 flex-1">{t("tranches.simulated")}</p>
        </div>
      ) : null}
    </div>
  );
}

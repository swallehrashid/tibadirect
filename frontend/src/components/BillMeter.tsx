import { useTranslation } from "react-i18next";
import { formatKes, formatUsd, percentOf } from "@/lib/money";
import { cn } from "@/lib/utils";

interface Props {
  /** Confirmed bill (or estimate) in KES minor units; null before the hospital has confirmed. */
  targetMinor: number | null;
  /** All donations counted so far, net of refunds; includes money already paid to the hospital. */
  pledgedMinor: number;
  /** Paid to the hospital. */
  settledMinor: number;
  isEstimate?: boolean;
  usdGoalMinor?: number | null;
  className?: string;
  compact?: boolean;
}

/**
 * A two-layer bar: pledged money (sky) sits behind money already paid to the hospital (blue).
 * Settled is part of pledged, so the settled layer is never drawn longer than the pledged one.
 * The same facts are written out in words for screen readers and updated politely.
 */
export function BillMeter({ targetMinor, pledgedMinor, settledMinor, isEstimate = false, usdGoalMinor, className, compact = false }: Props) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const settled = Math.min(settledMinor, pledgedMinor);
  const pledgedPercent = percentOf(pledgedMinor, targetMinor);
  const settledPercent = percentOf(settled, targetMinor);
  const summary =
    targetMinor === null
      ? t("meter.summaryNone", { pledged: formatKes(pledgedMinor, lang), settled: formatKes(settledMinor, lang) })
      : t("meter.summary", {
          pledged: formatKes(pledgedMinor, lang),
          settled: formatKes(settledMinor, lang),
          target: formatKes(targetMinor, lang),
        });

  return (
    <div className={cn("space-y-3", className)} data-testid="bill-meter">
      {targetMinor === null ? (
        <p className="text-sm text-muted-foreground">{t("meter.noTarget")}</p>
      ) : (
        <div>
          <p className="text-sm text-muted-foreground">{isEstimate ? t("meter.targetEstimate") : t("meter.target")}</p>
          <p className={cn("font-heading font-bold tracking-tight", compact ? "text-xl" : "text-3xl")}>{formatKes(targetMinor, lang)}</p>
          {usdGoalMinor ? <p className="text-sm text-muted-foreground">{t("meter.usdGoal", { usd: formatUsd(usdGoalMinor) })}</p> : null}
        </div>
      )}

      <div
        role="meter"
        aria-label={t("meter.pledged")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pledgedPercent}
        aria-valuetext={summary}
        className={cn("relative w-full overflow-hidden rounded-full bg-tiba-100", compact ? "h-2.5" : "h-4")}
      >
        <div className="absolute inset-y-0 left-0 bg-sky-500 transition-[width] duration-500" style={{ width: `${pledgedPercent}%` }} data-layer="pledged" />
        <div className="absolute inset-y-0 left-0 bg-primary transition-[width] duration-500" style={{ width: `${settledPercent}%` }} data-layer="settled" />
      </div>

      <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <div className="flex items-center gap-2">
          <span className="size-3 rounded-full bg-sky-500" aria-hidden="true" />
          <dt className="text-muted-foreground">{t("meter.pledged")}</dt>
          <dd className="font-semibold">{formatKes(pledgedMinor, lang)}</dd>
        </div>
        <div className="flex items-center gap-2">
          <span className="size-3 rounded-full bg-primary" aria-hidden="true" />
          <dt className="text-muted-foreground">{t("meter.settled")}</dt>
          <dd className="font-semibold">{formatKes(settledMinor, lang)}</dd>
        </div>
      </dl>

      <p className="sr-only" aria-live="polite">
        {summary}
      </p>
    </div>
  );
}

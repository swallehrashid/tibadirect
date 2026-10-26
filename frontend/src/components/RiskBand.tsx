import { AlertTriangle, CheckCircle2, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { RiskBandValue } from "@/api/enums";
import { Badge } from "@/components/ui/badge";

const TONE = { low: "info", medium: "pending", high: "danger" } as const;
const ICON = { low: CheckCircle2, medium: Search, high: AlertTriangle } as const;

/** The check result for staff and admins. Words and icon carry the meaning, not colour alone. */
export function RiskBand({ band }: { band: RiskBandValue }) {
  const { t } = useTranslation();
  const Icon = ICON[band];
  return (
    <Badge tone={TONE[band]} data-risk-band={band}>
      <Icon aria-hidden="true" />
      <span className="sr-only">{t("risk.label")}: </span>
      {t(`risk.${band}`)}
    </Badge>
  );
}

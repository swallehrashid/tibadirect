import { useTranslation } from "react-i18next";
import type { CaseStatus } from "@/api/enums";
import { Badge } from "@/components/ui/badge";
import { STATUS_TONE } from "@/lib/caseStatus";

export function StatusBadge({ status }: { status: CaseStatus }) {
  const { t } = useTranslation();
  return (
    <Badge tone={STATUS_TONE[status]} data-status={status}>
      {t(`caseStatus.${status}`)}
    </Badge>
  );
}

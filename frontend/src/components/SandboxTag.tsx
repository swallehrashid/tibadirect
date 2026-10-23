import { FlaskConical } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";

/** Marks any step whose money or hospital response is simulated. */
export function SandboxTag({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <Badge tone="pending" className={className}>
      <FlaskConical aria-hidden="true" />
      {t("demo.tag")}
    </Badge>
  );
}

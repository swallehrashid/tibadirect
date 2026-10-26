import { CircleHelp, Clock, ShieldCheck, type LucideIcon } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { TrustLevel } from "@/api/enums";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const TONE = { hospital_confirmed: "confirmed", pending_confirmation: "pending", unverified: "neutral" } as const;
const ICON: Record<TrustLevel, LucideIcon> = {
  hospital_confirmed: ShieldCheck,
  pending_confirmation: Clock,
  unverified: CircleHelp,
};

interface Props {
  level: TrustLevel;
  /** Show the one-line explanation under the badge (the case page does; cards keep it short). */
  explain?: boolean;
  className?: string;
}

/**
 * The trust badge is never colour alone: each level has its own icon and words. The explanation
 * is plain text rather than a hover tip so it also works on a phone.
 */
export function TrustBadge({ level, explain = false, className }: Props) {
  const { t } = useTranslation();
  const Icon = ICON[level];
  const explanationId = useId();
  return (
    <div className={cn("flex flex-col items-start gap-1.5", className)}>
      <Badge tone={TONE[level]} aria-describedby={explain ? explanationId : undefined} data-trust-level={level}>
        <Icon aria-hidden="true" />
        {t(`trust.${level}.label`)}
      </Badge>
      {explain ? (
        <p id={explanationId} className="text-sm text-muted-foreground">
          {t(`trust.${level}.explain`)}
        </p>
      ) : (
        <span className="sr-only">{t(`trust.${level}.explain`)}</span>
      )}
    </div>
  );
}

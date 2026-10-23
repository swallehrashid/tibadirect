import { Inbox } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

interface Props {
  title?: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
  /** h1 when this replaces the whole page, otherwise h2. */
  headingLevel?: 1 | 2;
}

export function EmptyState({ title, description, icon, action, className, headingLevel = 2 }: Props) {
  const { t } = useTranslation();
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-lg border border-dashed border-input bg-card px-6 py-10 text-center", className)}>
      <div className="text-primary" aria-hidden="true">
        {icon ?? <Inbox className="size-10" />}
      </div>
      <Heading className="font-heading text-lg font-semibold">{title ?? t("empty.title")}</Heading>
      {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
      {action}
    </div>
  );
}

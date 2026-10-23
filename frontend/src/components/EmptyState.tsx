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
}

export function EmptyState({ title, description, icon, action, className }: Props) {
  const { t } = useTranslation();
  return (
    <div className={cn("flex flex-col items-center gap-3 rounded-lg border border-dashed border-input bg-card px-6 py-10 text-center", className)}>
      <div className="text-primary" aria-hidden="true">
        {icon ?? <Inbox className="size-10" />}
      </div>
      <h3 className="font-heading text-lg font-semibold">{title ?? t("empty.title")}</h3>
      {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
      {action}
    </div>
  );
}

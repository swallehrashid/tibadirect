import { TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/api/errors";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface Props {
  error?: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}

/** A friendly error with a retry button and, when the API gave one, the reference to quote. */
export function ErrorState({ error, onRetry, title, className }: Props) {
  const { t } = useTranslation();
  let body = t("error.body");
  let reference: string | null = null;
  let retryable = true;
  if (error instanceof ApiError) {
    reference = error.requestId;
    if (error.status === 429) body = t("error.rateLimited", { seconds: error.retryAfter ?? 30 });
    else if (error.status === 404) {
      body = t("error.notFound");
      retryable = false;
    } else if (error.status >= 400 && error.status < 500) body = error.message;
  } else if (error instanceof TypeError) {
    body = t("error.offline");
  }
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-3 rounded-lg border border-danger-600/30 bg-danger-50 px-6 py-10 text-center", className)}>
      <TriangleAlert className="size-10 text-danger-700" aria-hidden="true" />
      <h3 className="font-heading text-lg font-semibold text-danger-700">{title ?? t("error.title")}</h3>
      <p className="max-w-md text-sm">{body}</p>
      {reference ? <p className="text-xs text-muted-foreground">{t("error.reference", { id: reference })}</p> : null}
      {onRetry && retryable ? (
        <Button variant="outline" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

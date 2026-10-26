import { Check, Circle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export interface TimelineStep {
  key: string;
  label: string;
  at?: string | null;
  state: "done" | "current" | "upcoming";
}

function formatDate(value: string, language: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(language.startsWith("sw") ? "sw-KE" : "en-KE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

/** A vertical list of steps; the current one is marked for assistive technology as well. */
export function StatusTimeline({ steps, className }: { steps: TimelineStep[]; className?: string }) {
  const { i18n } = useTranslation();
  return (
    <ol className={cn("space-y-0", className)}>
      {steps.map((step, index) => (
        <li key={step.key} className="relative flex gap-3 pb-5 last:pb-0" aria-current={step.state === "current" ? "step" : undefined} data-state={step.state}>
          {index < steps.length - 1 ? (
            <span className={cn("absolute left-[11px] top-6 h-[calc(100%-1.5rem)] w-0.5", step.state === "done" ? "bg-uzima-600" : "bg-border")} aria-hidden="true" />
          ) : null}
          <span
            className={cn(
              "relative z-10 mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border-2",
              step.state === "done" && "border-uzima-600 bg-uzima-600 text-white",
              step.state === "current" && "border-primary bg-card text-primary",
              step.state === "upcoming" && "border-border bg-card text-muted-foreground",
            )}
            aria-hidden="true"
          >
            {step.state === "done" ? <Check className="size-3.5" /> : <Circle className={cn("size-2.5", step.state === "current" && "fill-current")} />}
          </span>
          <div>
            <p className={cn("text-sm leading-snug", step.state === "upcoming" ? "text-muted-foreground" : "font-semibold")}>{step.label}</p>
            {step.at ? <p className="text-xs text-muted-foreground">{formatDate(step.at, i18n.language)}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

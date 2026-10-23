import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

interface Props {
  steps: string[];
  /** Zero-based index of the step being shown. */
  current: number;
  className?: string;
}

/** Step indicator for the wizard. On a phone it reads "Step 2 of 5" with a segmented bar. */
export function Stepper({ steps, current, className }: Props) {
  const { t } = useTranslation();
  return (
    <nav aria-label={t("stepper.label")} className={cn("space-y-3", className)}>
      <p className="font-semibold sm:hidden" aria-live="polite">
        {t("stepper.stepOf", { current: current + 1, total: steps.length })}: {steps[current]}
      </p>
      <ol className="flex gap-1.5 sm:gap-0">
        {steps.map((label, index) => {
          const state = index < current ? "done" : index === current ? "current" : "upcoming";
          return (
            <li key={label} className="flex-1" aria-current={state === "current" ? "step" : undefined} data-state={state}>
              <div className={cn("h-1.5 rounded-full", state === "upcoming" ? "bg-tiba-100" : "bg-primary", "sm:hidden")} aria-hidden="true" />
              <div className="hidden items-center gap-2 sm:flex">
                <span
                  className={cn(
                    "flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                    state === "done" && "bg-uzima-700 text-white",
                    state === "current" && "bg-primary text-primary-foreground",
                    state === "upcoming" && "bg-tiba-100 text-tiba-900",
                  )}
                >
                  {state === "done" ? <Check className="size-4" aria-hidden="true" /> : index + 1}
                </span>
                <span className={cn("text-sm leading-tight", state === "current" ? "font-semibold" : "text-muted-foreground")}>
                  {label}
                  {state === "done" ? <span className="sr-only"> ({t("stepper.done")})</span> : null}
                </span>
                {index < steps.length - 1 ? <span className="mx-1 hidden h-px flex-1 bg-border lg:block" aria-hidden="true" /> : null}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

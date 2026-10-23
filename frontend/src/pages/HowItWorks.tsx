import { ArrowRight, Check, FlaskConical } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { SandboxTag } from "@/components/SandboxTag";
import { usePageTitle } from "@/lib/usePageTitle";
import { cn } from "@/lib/utils";

const TRUST_STEPS = ["s1", "s2", "s3", "s4"] as const;
const MONEY_STEPS = ["s1", "s2", "s3", "s4", "s5"] as const;
const REAL = ["r1", "r2", "r3", "r4"] as const;
const SIMULATED = ["m1", "m2", "m3", "m4"] as const;
const EXCESS = ["refund", "pool", "follow"] as const;
const FAQ = ["q1", "q2", "q3", "q4", "q5"] as const;

function Leg({ id, tone, steps }: { id: "trust" | "money"; tone: "trust" | "money"; steps: readonly string[] }) {
  const { t } = useTranslation();
  return (
    <Card className={cn("space-y-4 p-6", tone === "trust" ? "border-uzima-600/40" : "border-primary/40")}>
      <div className="space-y-1">
        <h3 className={cn("font-heading text-xl font-bold", tone === "trust" ? "text-uzima-700" : "text-tiba-700")}>{t(`howItWorks.${id}.title`)}</h3>
        <p className="text-sm text-muted-foreground">{t(`howItWorks.${id}.intro`)}</p>
      </div>
      <ol className="space-y-4">
        {steps.map((step, index) => (
          <li key={step} className="flex gap-3">
            <span
              className={cn(
                "flex size-8 shrink-0 items-center justify-center rounded-full text-sm font-bold text-white",
                tone === "trust" ? "bg-uzima-700" : "bg-primary",
              )}
              aria-hidden="true"
            >
              {index + 1}
            </span>
            <div>
              <h4 className="font-semibold leading-snug">{t(`howItWorks.${id}.${step}.title`)}</h4>
              <p className="text-sm text-muted-foreground">{t(`howItWorks.${id}.${step}.body`)}</p>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function HowItWorks() {
  const { t } = useTranslation();
  usePageTitle(t("howItWorks.title"));
  return (
    <div className="container space-y-12 py-8 md:py-12">
      <header className="max-w-3xl space-y-3">
        <h1 className="font-heading text-3xl font-extrabold md:text-4xl">{t("howItWorks.title")}</h1>
        <p className="text-lg text-muted-foreground">{t("howItWorks.intro")}</p>
      </header>

      <section aria-labelledby="two-legs" className="space-y-4">
        <h2 id="two-legs" className="sr-only">
          {t("howItWorks.legsTitle")}
        </h2>
        <div className="grid gap-6 lg:grid-cols-2">
          <Leg id="trust" tone="trust" steps={TRUST_STEPS} />
          <Leg id="money" tone="money" steps={MONEY_STEPS} />
        </div>
        <p className="font-heading text-sm font-semibold text-tiba-700">{t("howItWorks.motto")}</p>
      </section>

      <section aria-labelledby="excess" className="space-y-4">
        <div className="max-w-3xl space-y-1">
          <h2 id="excess" className="font-heading text-2xl font-bold">
            {t("howItWorks.excess.title")}
          </h2>
          <p className="text-muted-foreground">{t("howItWorks.excess.intro")}</p>
        </div>
        <ul className="grid gap-4 md:grid-cols-3">
          {EXCESS.map((key) => (
            <li key={key}>
              <Card className="h-full space-y-2 p-5">
                <h3 className="font-heading text-lg font-bold">{t(`howItWorks.excess.${key}.title`)}</h3>
                <p className="text-sm text-muted-foreground">{t(`howItWorks.excess.${key}.body`)}</p>
              </Card>
            </li>
          ))}
        </ul>
        <p className="max-w-3xl text-sm">{t("howItWorks.excess.freeze")}</p>
      </section>

      <section aria-labelledby="real" className="space-y-4">
        <h2 id="real" className="font-heading text-2xl font-bold">
          {t("howItWorks.real.title")}
        </h2>
        <div className="grid gap-6 md:grid-cols-2">
          <Card className="space-y-3 p-6">
            <h3 className="flex items-center gap-2 font-heading text-lg font-bold text-uzima-700">
              <Check className="size-5" aria-hidden="true" /> {t("howItWorks.real.realTitle")}
            </h3>
            <ul className="list-disc space-y-2 pl-5 text-sm">
              {REAL.map((key) => (
                <li key={key}>{t(`howItWorks.real.${key}`)}</li>
              ))}
            </ul>
          </Card>
          <Card className="space-y-3 border-jua-400/60 bg-jua-50 p-6">
            <h3 className="flex items-center gap-2 font-heading text-lg font-bold text-jua-700">
              <FlaskConical className="size-5" aria-hidden="true" /> {t("howItWorks.real.simTitle")} <SandboxTag />
            </h3>
            <ul className="list-disc space-y-2 pl-5 text-sm">
              {SIMULATED.map((key) => (
                <li key={key}>{t(`howItWorks.real.${key}`)}</li>
              ))}
            </ul>
          </Card>
        </div>
      </section>

      <section aria-labelledby="faq" className="max-w-3xl space-y-4">
        <h2 id="faq" className="font-heading text-2xl font-bold">
          {t("howItWorks.faq.title")}
        </h2>
        <div className="space-y-2">
          {FAQ.map((key) => (
            <details key={key} className="group rounded-lg border border-border bg-card px-4 py-1">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 py-2 font-semibold marker:hidden [&::-webkit-details-marker]:hidden">
                {t(`howItWorks.faq.${key}.q`)}
                <span className="text-primary transition-transform group-open:rotate-90" aria-hidden="true">
                  <ArrowRight className="size-4" />
                </span>
              </summary>
              <p className="pb-3 text-sm text-muted-foreground">{t(`howItWorks.faq.${key}.a`)}</p>
            </details>
          ))}
        </div>
      </section>

      <div className="flex flex-wrap gap-3">
        <Button asChild size="lg">
          <Link to="/cases">{t("landing.hero.browse")}</Link>
        </Button>
        <Button asChild size="lg" variant="outline">
          <Link to="/app/new">{t("landing.hero.start")}</Link>
        </Button>
      </div>
    </div>
  );
}

export const Component = HowItWorks;

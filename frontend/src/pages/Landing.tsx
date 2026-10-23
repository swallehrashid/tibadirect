import { ArrowRight, Building2, ChevronLeft, ChevronRight, FileText, HandCoins, ScanSearch, ShieldCheck } from "lucide-react";
import { useRef } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { usePublicCases, usePublicConfig, usePublicStats } from "@/api";
import { CaseCard, CaseCardSkeleton } from "@/components/CaseCard";
import { SandboxTag } from "@/components/SandboxTag";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatKes } from "@/lib/money";
import { usePageTitle } from "@/lib/usePageTitle";

const STEPS = [
  { key: "share", icon: FileText },
  { key: "confirm", icon: ShieldCheck },
  { key: "donate", icon: HandCoins },
  { key: "pay", icon: Building2 },
] as const;

function Stats() {
  const { t, i18n } = useTranslation();
  const { data, isPending, isError } = usePublicStats();
  if (isError) return null; // the numbers are a nicety; the page works without them
  const tiles = data
    ? [
        { label: t("landing.stats.cases"), value: String(data.public_cases) },
        { label: t("landing.stats.hospitals"), value: String(data.hospitals) },
        { label: t("landing.stats.donations"), value: String(data.donations) },
        { label: t("landing.stats.settled"), value: formatKes(data.settled_kes_minor, i18n.language) },
      ]
    : [];
  return (
    <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {isPending
        ? Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-20" />)
        : tiles.map((tile) => (
            <Card key={tile.label} className="p-4">
              <dd className="font-heading text-2xl font-extrabold tracking-tight">{tile.value}</dd>
              <dt className="text-sm text-muted-foreground">{tile.label}</dt>
            </Card>
          ))}
    </dl>
  );
}

function LiveCases() {
  const { t } = useTranslation();
  const { data, isPending, isError } = usePublicCases({ pageSize: 8 });
  const track = useRef<HTMLUListElement>(null);
  const scroll = (direction: 1 | -1) => track.current?.scrollBy({ left: direction * (track.current.clientWidth * 0.85), behavior: "smooth" });
  if (isError) return null;
  if (!isPending && data?.items.length === 0) return null;
  return (
    <section aria-labelledby="live-cases" className="container space-y-4 py-10">
      <div className="flex items-end justify-between gap-4">
        <h2 id="live-cases" className="font-heading text-2xl font-bold">
          {t("landing.live.title")}
        </h2>
        <div className="hidden gap-2 md:flex">
          <Button variant="outline" size="icon" aria-label={t("landing.live.previous")} onClick={() => scroll(-1)}>
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="icon" aria-label={t("landing.live.next")} onClick={() => scroll(1)}>
            <ChevronRight />
          </Button>
        </div>
      </div>
      <ul ref={track} className="-mx-4 flex snap-x snap-mandatory gap-4 overflow-x-auto px-4 pb-3" aria-busy={isPending}>
        {isPending
          ? Array.from({ length: 3 }, (_, index) => (
              <li key={index} className="w-[85%] shrink-0 sm:w-80">
                <CaseCardSkeleton />
              </li>
            ))
          : data?.items.map((item) => (
              <li key={item.slug} className="w-[85%] shrink-0 snap-start sm:w-80">
                <CaseCard item={item} />
              </li>
            ))}
      </ul>
      <Button asChild variant="link" className="px-0">
        <Link to="/cases">
          {t("landing.live.all")} <ArrowRight />
        </Link>
      </Button>
    </section>
  );
}

export function Landing() {
  const { t } = useTranslation();
  usePageTitle("");
  const { data: config } = usePublicConfig();
  return (
    <>
      <section className="bg-card">
        <div className="container grid gap-8 py-12 md:grid-cols-[3fr_2fr] md:items-center md:py-16">
          <div className="space-y-5">
            {config?.demo_banner ? <SandboxTag /> : null}
            <h1 className="font-heading text-4xl font-extrabold leading-tight tracking-tight md:text-5xl">{t("landing.hero.title")}</h1>
            <p className="max-w-xl text-lg text-muted-foreground">{t("landing.hero.body")}</p>
            <div className="flex flex-wrap gap-3">
              <Button asChild size="lg">
                <Link to="/cases">{t("landing.hero.browse")}</Link>
              </Button>
              <Button asChild size="lg" variant="outline">
                <Link to="/app/new">{t("landing.hero.start")}</Link>
              </Button>
            </div>
            <p className="font-heading text-sm font-semibold text-tiba-700">{t("landing.hero.motto")}</p>
          </div>
          <Card className="space-y-3 border-uzima-600/30 bg-uzima-50 p-6 shadow-none">
            <ShieldCheck className="size-8 text-uzima-700" aria-hidden="true" />
            <h2 className="font-heading text-xl font-bold text-uzima-700">{t("landing.promise.title")}</h2>
            <p>{t("landing.promise.body")}</p>
          </Card>
        </div>
      </section>

      <section aria-labelledby="how-money-flows" className="container space-y-6 py-12">
        <div className="space-y-1">
          <h2 id="how-money-flows" className="font-heading text-2xl font-bold">
            {t("landing.flow.title")}
          </h2>
          <p className="text-muted-foreground">{t("landing.flow.intro")}</p>
        </div>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ key, icon: Icon }, index) => (
            <li key={key}>
              <Card className="h-full space-y-3 p-5">
                <div className="flex items-center gap-3">
                  <span className="flex size-10 items-center justify-center rounded-full bg-tiba-100 text-tiba-700" aria-hidden="true">
                    <Icon className="size-5" />
                  </span>
                  <span className="font-heading text-sm font-bold text-muted-foreground">{t("landing.flow.step", { n: index + 1 })}</span>
                </div>
                <h3 className="font-heading text-lg font-bold leading-snug">{t(`landing.flow.${key}.title`)}</h3>
                <p className="text-sm text-muted-foreground">{t(`landing.flow.${key}.body`)}</p>
              </Card>
            </li>
          ))}
        </ol>
        <Button asChild variant="link" className="px-0">
          <Link to="/how-it-works">
            {t("landing.flow.more")} <ArrowRight />
          </Link>
        </Button>
      </section>

      <LiveCases />

      {config?.features.appeal_checker === false ? null : (
        <section aria-labelledby="check-appeal" className="bg-tiba-900 text-white">
          <div className="container flex flex-col gap-4 py-10 md:flex-row md:items-center md:justify-between">
            <div className="max-w-2xl space-y-2">
              <h2 id="check-appeal" className="flex items-center gap-2 font-heading text-2xl font-bold">
                <ScanSearch className="size-6 shrink-0 text-sky-500" aria-hidden="true" /> {t("landing.check.title")}
              </h2>
              <p className="text-tiba-100">{t("landing.check.body")}</p>
            </div>
            <Button asChild size="lg" className="bg-sky-500 text-tiba-900 hover:bg-sky-500/90">
              <Link to="/check">{t("landing.check.cta")}</Link>
            </Button>
          </div>
        </section>
      )}

      <section aria-labelledby="numbers" className="container space-y-4 py-12">
        <h2 id="numbers" className="font-heading text-2xl font-bold">
          {t("landing.stats.title")}
        </h2>
        <Stats />
        <p className="text-sm text-muted-foreground">{t("landing.stats.note")}</p>
      </section>
    </>
  );
}

export const Component = Landing;

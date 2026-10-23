import { Building2, MapPin, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { PublicCase } from "@/api";
import { BillMeter } from "@/components/BillMeter";
import { TrustBadge } from "@/components/TrustBadge";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

/** A public case in a list: who, where, how much is covered, and one link to the full page. */
export function CaseCard({ item }: { item: PublicCase }) {
  const { t } = useTranslation();
  return (
    <Card className="relative flex h-full flex-col gap-4 p-5 transition-shadow focus-within:ring-2 focus-within:ring-ring hover:shadow-md" data-testid="case-card">
      <div className="space-y-2">
        <TrustBadge level={item.trust_level} />
        <div className="min-w-0">
          <h3 className="font-heading text-lg font-bold leading-snug">
            {/* The whole card is the link target (the ::after covers it), so there is one link to hear, not several. */}
            <Link to={`/c/${item.slug}`} className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:ring-0">
              {item.alias}
            </Link>
          </h3>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground">
            <MapPin className="size-4 shrink-0" aria-hidden="true" />
            {item.region}
          </p>
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Building2 className="size-4 shrink-0" aria-hidden="true" />
            {item.hospital_name}
          </p>
        </div>
      </div>
      {item.story ? <p className="line-clamp-2 text-sm">{item.story}</p> : null}
      <BillMeter
        compact
        targetMinor={item.target_kes_minor}
        pledgedMinor={item.pledged_kes_minor}
        settledMinor={item.settled_kes_minor}
        isEstimate={item.is_estimate}
        className="mt-auto"
      />
      <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Users className="size-4" aria-hidden="true" />
          {t("cases.donors", { count: item.donor_count })}
        </span>
        {item.status === "funded" ? <Badge tone="confirmed">{t("caseStatus.funded")}</Badge> : null}
      </div>
    </Card>
  );
}

export function CaseCardSkeleton() {
  return (
    <Card className="space-y-4 p-5" aria-hidden="true">
      <div className="h-6 w-1/2 animate-pulse rounded-md bg-tiba-100" />
      <div className="h-4 w-2/3 animate-pulse rounded-md bg-tiba-100" />
      <div className="h-16 animate-pulse rounded-md bg-tiba-100" />
      <div className="h-4 w-1/3 animate-pulse rounded-md bg-tiba-100" />
    </Card>
  );
}

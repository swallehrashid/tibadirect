import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * One figure with its label, for dashboards; place inside a <dl>. With `to`, the label is a link
 * stretched over the whole tile, so there is one link to hear, not a link around a list item.
 */
export function StatTile({ label, value, hint, tone, to }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "attention"; to?: string }) {
  return (
    <Card className={cn("relative space-y-1 p-4", tone === "attention" && "border-jua-400 bg-jua-50", to && "transition-shadow focus-within:ring-2 focus-within:ring-ring hover:shadow-md")}>
      <dd className="font-heading text-xl font-extrabold tracking-tight [overflow-wrap:anywhere] sm:text-2xl">{value}</dd>
      <dt className="text-sm text-muted-foreground">
        {to ? (
          <Link to={to} className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:ring-0">
            {label}
          </Link>
        ) : (
          label
        )}
      </dt>
      {hint ? <dd className="text-xs text-muted-foreground">{hint}</dd> : null}
    </Card>
  );
}

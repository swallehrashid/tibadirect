import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A box that scrolls sideways when its content is wider than the screen. It takes keyboard focus, so arrow keys can scroll it. */
export function ScrollRegion({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scrollable region must be focusable to be reachable by keyboard (WCAG 2.1.1)
    <div role="region" aria-label={label} tabIndex={0} className={cn("overflow-x-auto", className)}>
      {children}
    </div>
  );
}

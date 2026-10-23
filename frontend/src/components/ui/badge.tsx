import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** Chips follow the fixed colour meaning: teal confirmed, amber pending, sky pledged/info, blue brand. */
export const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold leading-none [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        brand: "bg-tiba-100 text-tiba-900",
        confirmed: "bg-uzima-100 text-uzima-700",
        pending: "bg-jua-100 text-jua-700",
        info: "bg-sky-100 text-sky-800",
        danger: "bg-danger-50 text-danger-700",
        neutral: "bg-muted text-muted-foreground",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

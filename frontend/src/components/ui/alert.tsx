import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const alertVariants = cva("relative flex w-full gap-3 rounded-lg border p-4 text-sm [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0", {
  variants: {
    tone: {
      info: "border-sky-500/40 bg-sky-100 text-sky-800",
      success: "border-uzima-600/30 bg-uzima-50 text-uzima-700",
      warning: "border-jua-400/60 bg-jua-50 text-jua-700",
      danger: "border-danger-600/30 bg-danger-50 text-danger-700",
    },
  },
  defaultVariants: { tone: "info" },
});

export interface AlertProps extends HTMLAttributes<HTMLDivElement>, VariantProps<typeof alertVariants> {}

export const Alert = forwardRef<HTMLDivElement, AlertProps>(({ className, tone, role, ...props }, ref) => (
  <div ref={ref} role={role ?? (tone === "danger" ? "alert" : "status")} className={cn(alertVariants({ tone }), className)} {...props} />
));
Alert.displayName = "Alert";

export function AlertTitle({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("font-semibold leading-snug", className)} {...props} />;
}

export function AlertDescription({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("leading-snug", className)} {...props} />;
}

import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { Check } from "lucide-react";
import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import { cn } from "@/lib/utils";

export const Checkbox = forwardRef<
  ElementRef<typeof CheckboxPrimitive.Root>,
  ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>
>(({ className, ...props }, ref) => (
  <CheckboxPrimitive.Root
    ref={ref}
    className={cn(
      "peer inline-flex size-6 shrink-0 items-center justify-center rounded-sm border border-input bg-card text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60 data-[state=checked]:border-primary data-[state=checked]:bg-primary",
      className,
    )}
    {...props}
  >
    <CheckboxPrimitive.Indicator>
      <Check className="size-4" aria-hidden="true" />
    </CheckboxPrimitive.Indicator>
  </CheckboxPrimitive.Root>
));
Checkbox.displayName = "Checkbox";

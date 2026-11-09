import { ScrollRegion } from "@/components/ScrollRegion";
import { cn } from "@/lib/utils";

interface Props<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  /** Names the group for screen readers, for example "Show". */
  label: string;
  className?: string;
}

/**
 * A row of buttons that filter a list. Unlike tabs there is no panel to point at, so it is a
 * plain group of toggle buttons: each says whether it is pressed. Longer translations (Swahili
 * labels run longer than English) can make the row wider than a small phone, so it scrolls
 * sideways within itself rather than the whole page.
 */
export function SegmentedControl<T extends string>({ value, onChange, options, label, className }: Props<T>) {
  return (
    <ScrollRegion label={label} className="max-w-full">
      <div role="group" aria-label={label} className={cn("inline-flex items-center gap-1 rounded-lg bg-tiba-100 p-1", className)}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={value === option.value}
            onClick={() => onChange(option.value)}
            className={cn("inline-flex min-h-10 shrink-0 items-center justify-center rounded-md px-4 text-sm font-semibold text-tiba-900", value === option.value && "bg-card shadow-card")}
          >
            {option.label}
          </button>
        ))}
      </div>
    </ScrollRegion>
  );
}

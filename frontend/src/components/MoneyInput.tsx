import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Input } from "@/components/ui/input";
import { minorToInput, parseMinor, type Currency } from "@/lib/money";
import { cn } from "@/lib/utils";

interface Props {
  id?: string;
  /** Amount in minor units (cents), or null when empty or not a valid amount. */
  value: number | null;
  onChange: (minor: number | null) => void;
  currency?: Currency;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
  className?: string;
  placeholder?: string;
}

const SYMBOL: Record<Currency, string> = { USD: "$", KES: "KES" };

/**
 * Keeps what the person typed (so "12." stays "12." while they type) and reports the amount in
 * minor units. Letters and symbols are ignored as they are typed.
 */
export function MoneyInput({ id, value, onChange, currency = "USD", disabled, invalid, describedBy, className, placeholder }: Props) {
  const { t } = useTranslation();
  const [text, setText] = useState(minorToInput(value));
  const [seen, setSeen] = useState(value);

  // Follow outside changes (for example choosing a preset amount) without disturbing typing.
  // Adjusting state while rendering is the supported way to derive it from a changed prop.
  if (value !== seen) {
    setSeen(value);
    if (parseMinor(text) !== value) setText(minorToInput(value));
  }

  return (
    <div className={cn("relative", className)}>
      <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-sm font-semibold text-muted-foreground" aria-hidden="true">
        {SYMBOL[currency]}
      </span>
      <Input
        id={id}
        value={text}
        inputMode="decimal"
        autoComplete="off"
        disabled={disabled}
        placeholder={placeholder}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        aria-label={id ? undefined : currency === "USD" ? t("money.usdLabel") : t("money.kesLabel")}
        className={currency === "USD" ? "pl-8" : "pl-12"}
        onChange={(event) => {
          const next = event.target.value.replace(/[^\d.,]/g, "");
          setText(next);
          onChange(parseMinor(next));
        }}
        onBlur={() => {
          const minor = parseMinor(text);
          if (minor !== null) setText(minorToInput(minor));
        }}
      />
    </div>
  );
}

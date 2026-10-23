import { useTranslation } from "react-i18next";
import { EXCESS_CHOICES, type ExcessChoice as ExcessChoiceValue } from "@/api/enums";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { cn } from "@/lib/utils";

interface Props {
  value: ExcessChoiceValue;
  onChange: (value: ExcessChoiceValue) => void;
  disabled?: boolean;
  idPrefix?: string;
}

/** What should happen to money the case does not end up needing, in the donor's own words. */
export function ExcessChoice({ value, onChange, disabled = false, idPrefix = "excess" }: Props) {
  const { t } = useTranslation();
  return (
    <fieldset className="space-y-3" disabled={disabled}>
      <legend className="text-sm font-semibold">{t("donate.excess.title")}</legend>
      <p className="text-sm text-muted-foreground">{t("donate.excess.intro")}</p>
      <RadioGroup value={value} onValueChange={(next) => onChange(next as ExcessChoiceValue)} disabled={disabled} aria-label={t("donate.excess.title")}>
        {EXCESS_CHOICES.map((choice) => {
          const id = `${idPrefix}-${choice}`;
          return (
            <div key={choice} className={cn("flex items-start gap-3 rounded-lg border p-3", value === choice ? "border-primary bg-tiba-50" : "border-border")}>
              <RadioGroupItem value={choice} id={id} className="mt-0.5" />
              <Label htmlFor={id} className="cursor-pointer space-y-0.5 font-normal">
                <span className="block font-semibold">{t(`donate.excess.${choice}.label`)}</span>
                <span className="block text-sm text-muted-foreground">{t(`donate.excess.${choice}.body`)}</span>
              </Label>
            </div>
          );
        })}
      </RadioGroup>
    </fieldset>
  );
}

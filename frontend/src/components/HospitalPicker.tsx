import { Building2, Search } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { HospitalListItem } from "@/api";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { cn } from "@/lib/utils";

interface Props {
  hospitals: HospitalListItem[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
}

/** Find a hospital by name or county, then choose it. A radio list, so it works with a keyboard and a screen reader. */
export function HospitalPicker({ hospitals, value, onChange, disabled = false, invalid = false, describedBy }: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const searchId = useId();
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = needle ? hospitals.filter((h) => `${h.display_name} ${h.county}`.toLowerCase().includes(needle)) : hospitals;
    // Keep the chosen hospital visible even when the search no longer matches it.
    const chosen = hospitals.find((h) => h.id === value);
    return chosen && !matches.includes(chosen) ? [chosen, ...matches] : matches;
  }, [hospitals, query, value]);

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor={searchId}>{t("wizard.hospital.search")}</Label>
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input id={searchId} type="search" className="pl-9" value={query} disabled={disabled} onChange={(event) => setQuery(event.target.value)} placeholder={t("wizard.hospital.searchPlaceholder")} />
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {t("wizard.hospital.results", { count: shown.length })}
      </p>
      {shown.length === 0 ? (
        <p className="rounded-lg border border-dashed border-input p-4 text-sm text-muted-foreground">{t("wizard.hospital.none")}</p>
      ) : (
        <RadioGroup value={value} onValueChange={onChange} disabled={disabled} aria-label={t("wizard.hospital.label")} aria-invalid={invalid || undefined} aria-describedby={describedBy} className="max-h-72 overflow-y-auto pr-1">
          {shown.map((hospital) => {
            const id = `hospital-${hospital.id}`;
            return (
              <div key={hospital.id} className={cn("flex items-center gap-3 rounded-lg border p-3", value === hospital.id ? "border-primary bg-tiba-50" : invalid ? "border-danger-600" : "border-border")}>
                <RadioGroupItem value={hospital.id} id={id} />
                <Label htmlFor={id} className="flex flex-1 cursor-pointer items-center gap-2 font-normal">
                  <Building2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span>
                    <span className="block font-semibold">{hospital.display_name}</span>
                    <span className="block text-sm text-muted-foreground">{hospital.county}</span>
                  </span>
                </Label>
              </div>
            );
          })}
        </RadioGroup>
      )}
    </div>
  );
}

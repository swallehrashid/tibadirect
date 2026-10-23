import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  /** The full wording the person is agreeing to, shown on request. */
  text: string;
  required: boolean;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** The patient is a minor, so the family member is consenting as a guardian. */
  guardian?: boolean;
  disabled?: boolean;
  /** Extra controls, such as a "Withdraw" button on the dashboard. */
  footer?: ReactNode;
}

/** One consent scope: its purpose in a line, the full wording one tap away, and a clear tick box. */
export function ConsentCard({ title, text, required, checked, onCheckedChange, guardian = false, disabled = false, footer }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const checkId = useId();
  const textId = useId();
  return (
    <Card className={cn(checked && "border-uzima-600")}>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-heading text-base font-semibold">{title}</h3>
          <Badge tone={required ? "pending" : "neutral"}>{required ? t("consent.required") : t("consent.optional")}</Badge>
        </div>
        {guardian ? <p className="text-sm font-semibold text-tiba-700">{t("consent.guardian")}</p> : null}
        <button
          type="button"
          className="inline-flex min-h-10 items-center gap-1 text-sm font-semibold text-primary"
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          {open ? t("consent.hide") : t("consent.read")}
        </button>
        <div id={textId} hidden={!open} className="max-h-64 overflow-y-auto whitespace-pre-line rounded-md bg-muted p-3 text-sm leading-relaxed">
          {text}
        </div>
        <div className="flex items-start gap-3">
          <Checkbox id={checkId} checked={checked} disabled={disabled} onCheckedChange={(value) => onCheckedChange(value === true)} />
          <label htmlFor={checkId} className="pt-0.5 text-sm font-semibold leading-snug">
            {t("consent.agree")}: {title}
          </label>
        </div>
        {footer}
      </CardContent>
    </Card>
  );
}

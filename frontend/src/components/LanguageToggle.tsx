import { useTranslation } from "react-i18next";
import { LANGUAGES, setLanguage, type Language } from "@/i18n";
import { cn } from "@/lib/utils";

/** English / Kiswahili switch for the header; each name is written in its own language. */
export function LanguageToggle({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const active: Language = i18n.language.startsWith("sw") ? "sw" : "en";
  return (
    <div role="group" aria-label={t("language.switchTo")} className={cn("inline-flex rounded-md border border-input bg-card p-0.5", className)}>
      {LANGUAGES.map((code) => (
        <button
          key={code}
          type="button"
          lang={code}
          aria-pressed={active === code}
          onClick={() => setLanguage(code)}
          className={cn(
            "min-h-10 min-w-12 rounded-sm px-3 text-sm font-semibold",
            active === code ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-tiba-100",
          )}
        >
          {code === "en" ? "EN" : "SW"}
          <span className="sr-only"> {t(`language.${code}`)}</span>
        </button>
      ))}
    </div>
  );
}

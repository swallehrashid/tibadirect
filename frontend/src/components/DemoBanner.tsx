import { FlaskConical } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Shown across the top while the app runs on sandbox money. */
export function DemoBanner() {
  const { t } = useTranslation();
  return (
    <div role="note" className="bg-jua-400 text-tiba-900">
      <p className="container flex items-center gap-2 py-2 text-sm font-semibold">
        <FlaskConical className="size-4 shrink-0" aria-hidden="true" />
        {t("demo.banner")}
      </p>
    </div>
  );
}

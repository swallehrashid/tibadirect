import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface Props {
  page: number;
  totalPages: number;
  onPage: (page: number) => void;
  /** Names the navigation for screen readers. */
  label?: string;
}

/** Previous / page x of y / Next. It wraps on a narrow screen, where longer Swahili labels would not fit one line. */
export function Pager({ page, totalPages, onPage, label }: Props) {
  const { t } = useTranslation();
  if (totalPages <= 1) return null;
  return (
    <nav aria-label={label ?? t("cases.pages")} className="flex flex-wrap items-center justify-between gap-3">
      <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        {t("cases.previous")}
      </Button>
      <span className="text-sm text-muted-foreground">{t("cases.pageOf", { page, total: totalPages })}</span>
      <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onPage(page + 1)}>
        {t("cases.next")}
      </Button>
    </nav>
  );
}

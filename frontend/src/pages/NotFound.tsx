import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import { usePageTitle } from "@/lib/usePageTitle";

export function NotFound() {
  const { t } = useTranslation();
  usePageTitle(t("notFound.title"));
  return (
    <div className="container py-16">
      <EmptyState
        title={t("notFound.title")}
        description={t("notFound.body")}
        action={
          <Button asChild>
            <Link to="/">{t("auth.backHome")}</Link>
          </Button>
        }
      />
    </div>
  );
}

export const Component = NotFound;

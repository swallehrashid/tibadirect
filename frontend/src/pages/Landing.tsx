import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { usePageTitle } from "@/lib/usePageTitle";

// Replaced by the real landing page in P4-F04.
export function Landing() {
  const { t } = useTranslation();
  usePageTitle("");
  return (
    <div className="container space-y-6 py-16">
      <h1 className="max-w-2xl font-heading text-4xl font-extrabold">{t("app.tagline")}</h1>
      <Button asChild size="lg">
        <Link to="/login">{t("nav.signIn")}</Link>
      </Button>
    </div>
  );
}

export const Component = Landing;

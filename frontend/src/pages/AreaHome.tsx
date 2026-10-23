import { useTranslation } from "react-i18next";
import type { UserRole } from "@/api/enums";
import { useAuth } from "@/auth/AuthContext";
import { usePageTitle } from "@/lib/usePageTitle";

// Stand-in landing screen for each signed-in area until its real screens arrive (P4-F06 to F08).
export function AreaHome({ area }: { area: UserRole }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  usePageTitle(t(`shell.area.${area}`));
  return (
    <div className="space-y-2">
      <h1 className="font-heading text-2xl font-bold">{t("shell.welcome", { name: user?.display_name ?? "" })}</h1>
      <p className="text-muted-foreground">{t(`shell.area.${area}`)}</p>
    </div>
  );
}

import { Link, NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { usePublicConfig } from "@/api";
import type { UserRole } from "@/api/enums";
import { useAuth } from "@/auth/AuthContext";
import { DemoBanner } from "@/components/DemoBanner";
import { LanguageToggle } from "@/components/LanguageToggle";
import { Logo } from "@/components/Logo";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { SkipLink } from "./SkipLink";
import { UserMenu } from "./UserMenu";

interface NavItem {
  to: string;
  label: string;
  /** Match only this exact path (the area's home), not everything beneath it. */
  end?: boolean;
}

function useNav(role: UserRole): NavItem[] {
  const { t } = useTranslation();
  const { data: config } = usePublicConfig();
  switch (role) {
    case "family":
      return [
        { to: "/app", label: t("nav.myCases"), end: true },
        { to: "/app/new", label: t("nav.startCampaign") },
      ];
    case "hospital_staff":
      return [
        { to: "/hospital", label: t("nav.queue"), end: true },
        { to: "/hospital/settlements", label: t("nav.settlements") },
      ];
    case "admin":
      return [
        { to: "/admin", label: t("nav.overview"), end: true },
        { to: "/admin/review", label: t("nav.review") },
        { to: "/admin/audit", label: t("nav.audit") },
        { to: "/admin/jobs", label: t("nav.jobs") },
        ...(config?.features.agent ? [{ to: "/admin/agent", label: t("nav.agent") }] : []),
      ];
  }
}

/** Layout for the signed-in areas: a header with who is signed in, and a navigation row for the role. */
export function AppShell({ area: role }: { area: UserRole }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { data: config } = usePublicConfig();
  const nav = useNav(role);
  return (
    <div className="flex min-h-dvh flex-col">
      <SkipLink />
      {config?.demo_banner ? <DemoBanner /> : null}
      <header className="border-b border-border bg-card">
        <div className="container flex h-16 items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Link to="/" aria-label={t("shell.home")} className="shrink-0 rounded-md">
              <Logo />
            </Link>
            <Badge tone="brand" className="hidden sm:inline-flex">
              {t(`shell.area.${role}`)}
              {role === "hospital_staff" && user?.hospital ? `: ${user.hospital.display_name}` : ""}
            </Badge>
          </div>
          <div className="flex items-center gap-2">
            <LanguageToggle className="hidden sm:inline-flex" />
            <UserMenu />
          </div>
        </div>
        <nav aria-label={t(`shell.area.${role}`)} className="container flex gap-1 overflow-x-auto pb-2">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn("shrink-0 rounded-md px-3 py-2.5 text-sm font-semibold hover:bg-tiba-100", isActive && "bg-primary text-primary-foreground hover:bg-primary")
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main id="main" tabIndex={-1} className="container flex-1 py-6 outline-none md:py-8">
        <Outlet />
      </main>
    </div>
  );
}

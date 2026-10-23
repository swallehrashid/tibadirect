import { Menu } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { usePublicConfig } from "@/api";
import { useAuth } from "@/auth/AuthContext";
import { homeFor } from "@/auth/roles";
import { DemoBanner } from "@/components/DemoBanner";
import { LanguageToggle } from "@/components/LanguageToggle";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { SkipLink } from "./SkipLink";
import { UserMenu } from "./UserMenu";

function useLinks() {
  const { t } = useTranslation();
  const { data: config } = usePublicConfig();
  return [
    { to: "/cases", label: t("nav.cases") },
    { to: "/how-it-works", label: t("nav.howItWorks") },
    ...(config?.features.appeal_checker === false ? [] : [{ to: "/check", label: t("nav.check") }]),
  ];
}

/** Layout for public pages: header, the page, and a footer with the legal links. */
export function PageShell() {
  const { t } = useTranslation();
  const { user, status } = useAuth();
  const { data: config } = usePublicConfig();
  const links = useLinks();
  return (
    <div className="flex min-h-dvh flex-col">
      <SkipLink />
      {config?.demo_banner ? <DemoBanner /> : null}
      <header className="sticky top-0 z-40 border-b border-border bg-card">
        <div className="container flex h-16 items-center justify-between gap-3">
          <Link to="/" aria-label={t("shell.home")} className="rounded-md">
            <Logo />
          </Link>
          <nav aria-label={t("shell.mainNav")} className="hidden items-center gap-1 md:flex">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) => cn("rounded-md px-3 py-2 text-sm font-semibold hover:bg-tiba-100", isActive && "bg-tiba-100")}
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <LanguageToggle className="hidden sm:inline-flex" />
            {user ? (
              <UserMenu />
            ) : status === "loading" ? null : (
              <Button asChild size="sm">
                <Link to="/login">{t("nav.signIn")}</Link>
              </Button>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" className="md:hidden" aria-label={t("shell.menu")}>
                  <Menu />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {links.map((link) => (
                  <DropdownMenuItem key={link.to} asChild>
                    <Link to={link.to}>{link.label}</Link>
                  </DropdownMenuItem>
                ))}
                {user ? (
                  <DropdownMenuItem asChild>
                    <Link to={homeFor(user.role)}>{t("shell.myArea")}</Link>
                  </DropdownMenuItem>
                ) : null}
                <div className="px-2 py-2 sm:hidden">
                  <LanguageToggle />
                </div>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="flex-1 outline-none">
        <Outlet />
      </main>
      <footer className="bg-tiba-900 text-white">
        <div className="container flex flex-col gap-6 py-10 md:flex-row md:items-start md:justify-between">
          <div className="max-w-sm space-y-3">
            <Logo onDark />
            <p className="text-sm text-tiba-100">{t("app.tagline")}</p>
          </div>
          <nav aria-label={t("shell.footerNav")} className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {links.map((link) => (
              <Link key={link.to} to={link.to} className="rounded-sm py-2 text-tiba-100 underline-offset-4 hover:text-white hover:underline">
                {link.label}
              </Link>
            ))}
            <Link to="/privacy" className="rounded-sm py-2 text-tiba-100 underline-offset-4 hover:text-white hover:underline">
              {t("nav.privacy")}
            </Link>
            <Link to="/terms" className="rounded-sm py-2 text-tiba-100 underline-offset-4 hover:text-white hover:underline">
              {t("nav.terms")}
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}

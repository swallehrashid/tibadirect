import { ChevronDown, LogOut, UserRound } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { homeFor } from "@/auth/roles";
import { LanguageToggle } from "@/components/LanguageToggle";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

/** Account menu for signed-in people: who they are, a link to their area, and sign out. */
export function UserMenu() {
  const { t } = useTranslation();
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" aria-label={t("shell.accountMenu")}>
          <UserRound />
          <span className="hidden max-w-32 truncate sm:inline">{user.display_name}</span>
          <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>
          <span className="block text-sm font-semibold text-foreground">{user.display_name}</span>
          <span className="block break-all font-normal">{user.email}</span>
        </DropdownMenuLabel>
        <div className="px-2 py-2 sm:hidden">
          <LanguageToggle />
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => navigate(homeFor(user.role))}>{t("shell.myArea")}</DropdownMenuItem>
        <DropdownMenuItem
          // A guarded page sends the visitor home once the session ends; public pages stay where they are.
          onSelect={() => void signOut()}
        >
          <LogOut className="size-4" aria-hidden="true" /> {t("shell.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

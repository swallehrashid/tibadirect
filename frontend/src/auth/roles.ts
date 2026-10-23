import type { UserRole } from "@/api/enums";

/** Where each role lands after signing in, and the only area it may use. */
export const HOME_BY_ROLE: Record<UserRole, string> = {
  family: "/app",
  hospital_staff: "/hospital",
  admin: "/admin",
};

export function homeFor(role: UserRole): string {
  return HOME_BY_ROLE[role];
}

/**
 * A "next" address from the query string is only followed when it stays on this site and inside
 * the area the signed-in role may use. Anything else (another site, "//evil", a different role's
 * area) falls back to the role's own home.
 */
export function safeNext(next: string | null | undefined, role: UserRole): string {
  const home = homeFor(role);
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) return home;
  const inArea = next === home || next.startsWith(`${home}/`) || next.startsWith(`${home}?`);
  const isPublic = !Object.values(HOME_BY_ROLE).some((area) => next === area || next.startsWith(`${area}/`) || next.startsWith(`${area}?`));
  return inArea || (isPublic && next !== "/login" && !next.startsWith("/login/")) ? next : home;
}

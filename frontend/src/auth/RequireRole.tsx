import { Navigate, Outlet, useLocation } from "react-router-dom";
import type { UserRole } from "@/api/enums";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "./AuthContext";
import { homeFor } from "./roles";

/**
 * Guards a group of routes. Signed-out visitors go to the sign-in page and come back afterwards;
 * someone signed in with another role is sent to their own area. The server still decides what
 * anyone may read; this only keeps people from landing on screens that cannot work for them.
 */
export function RequireRole({ area }: { area: UserRole }) {
  const { user, status, leftOnPurpose } = useAuth();
  const location = useLocation();
  if (status === "loading") {
    return (
      <div className="container py-10" aria-busy="true">
        <Skeleton className="h-8 w-48" />
      </div>
    );
  }
  if (!user) {
    if (leftOnPurpose) return <Navigate to="/" replace />;
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (user.role !== area) return <Navigate to={homeFor(user.role)} replace />;
  return <Outlet />;
}

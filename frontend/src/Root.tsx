import { useEffect, useRef } from "react";
import { Outlet, ScrollRestoration, useLocation } from "react-router-dom";
import { AuthProvider } from "@/auth/AuthContext";

/**
 * Top of the route tree. After each navigation it moves keyboard focus to the main content and
 * lets screen readers know the page changed (the first load is left alone).
 */
function RouteFocus() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [pathname]);
  return null;
}

export function Root() {
  return (
    <AuthProvider>
      <RouteFocus />
      <Outlet />
      <ScrollRestoration />
    </AuthProvider>
  );
}

import { createBrowserRouter } from "react-router-dom";
import { RequireRole } from "@/auth/RequireRole";
import { AppShell } from "@/layouts/AppShell";
import { PageShell } from "@/layouts/PageShell";
import { AreaHome } from "@/pages/AreaHome";
import { Root } from "./Root";

// Pages load on demand (route-level code splitting); shells and guards are part of the main bundle.
export const routes = [
  {
    element: <Root />,
    children: [
      {
        element: <PageShell />,
        children: [
          { path: "/", lazy: () => import("@/pages/Landing") },
          { path: "/cases", lazy: () => import("@/pages/Cases") },
          { path: "/c/:slug", lazy: () => import("@/pages/CasePage") },
          { path: "/receipt/:token", lazy: () => import("@/pages/ReceiptPage") },
          { path: "/how-it-works", lazy: () => import("@/pages/HowItWorks") },
          { path: "/privacy", lazy: () => import("@/pages/Privacy") },
          { path: "/terms", lazy: () => import("@/pages/Terms") },
          { path: "/login", lazy: () => import("@/pages/Login") },
          { path: "/login/verify", lazy: () => import("@/pages/LoginVerify") },
          // The showcase is only downloaded when someone opens it.
          { path: "/design", lazy: () => import("@/pages/Design") },
          { path: "*", lazy: () => import("@/pages/NotFound") },
        ],
      },
      {
        element: <RequireRole area="family" />,
        children: [{ element: <AppShell area="family" />, children: [{ path: "/app", element: <AreaHome area="family" /> }] }],
      },
      {
        element: <RequireRole area="hospital_staff" />,
        children: [{ element: <AppShell area="hospital_staff" />, children: [{ path: "/hospital", element: <AreaHome area="hospital_staff" /> }] }],
      },
      {
        element: <RequireRole area="admin" />,
        children: [{ element: <AppShell area="admin" />, children: [{ path: "/admin", element: <AreaHome area="admin" /> }] }],
      },
    ],
  },
];

export const router = createBrowserRouter(routes);

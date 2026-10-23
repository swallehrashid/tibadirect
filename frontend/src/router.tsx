import { createBrowserRouter } from "react-router-dom";
import { Home } from "./App";

export const router = createBrowserRouter([
  { path: "/", element: <Home /> },
  // Route-level code splitting: the showcase is only downloaded when someone opens it.
  { path: "/design", lazy: () => import("./pages/Design") },
]);

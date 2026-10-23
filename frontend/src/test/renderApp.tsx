import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/toaster";
import { createQueryClient } from "@/lib/queryClient";
import { routes } from "@/router";

/** Renders the real route tree at an address, with a fresh query cache. */
export function renderApp(initialPath: string) {
  const queryClient = createQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [initialPath] });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { ...view, router, queryClient };
}

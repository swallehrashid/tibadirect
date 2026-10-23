import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { createQueryClient } from "@/lib/queryClient";
import { Home } from "./App";

describe("Home", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows that the service is running once the health check answers", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), { status: 200, headers: { "Content-Type": "application/json" } })),
    );
    render(
      <QueryClientProvider client={createQueryClient()}>
        <Home />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("The service is running.")).toBeInTheDocument();
  });
});

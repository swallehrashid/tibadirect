import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adminUser, familyUser, mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

describe("role routing", () => {
  it("sends a signed-out visitor to sign in and remembers where they were going", async () => {
    mockApi({ "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "Sign in." } } } });
    const { router } = renderApp("/app");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(router.state.location.search).toBe("?next=%2Fapp");
  });

  it("shows a family member their own area", async () => {
    mockApi({ "GET /api/auth/me": familyUser });
    renderApp("/app");
    expect(await screen.findByRole("heading", { name: "My campaigns" })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Start a campaign" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Welcome, Demo Family 1")).toBeInTheDocument();
  });

  it("sends someone to their own area when they open another role's", async () => {
    mockApi({ "GET /api/auth/me": familyUser });
    const { router } = renderApp("/admin");
    expect(await screen.findByRole("heading", { name: "My campaigns" })).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  });

  it("shows the admin navigation, with the agent link only when the agent is on", async () => {
    mockApi({
      "GET /api/auth/me": adminUser,
      "GET /api/public/config": { paypal_client_id: "", paypal_mode: "mock", currency: "USD", demo_banner: false, features: { agent: true, appeal_checker: true } },
    });
    renderApp("/admin");
    expect(await screen.findByRole("link", { name: "Review queue" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Treasurer agent" })).toBeInTheDocument();
  });

  it("hides the agent link when the agent is off", async () => {
    mockApi({ "GET /api/auth/me": adminUser });
    renderApp("/admin");
    expect(await screen.findByRole("link", { name: "Review queue" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Treasurer agent" })).not.toBeInTheDocument();
  });

  it("signs out: tells the API, then shows the public site", async () => {
    const api = mockApi({ "GET /api/auth/me": familyUser, "POST /api/auth/logout": { status: 204 } });
    const { router } = renderApp("/app");
    await screen.findByRole("heading", { name: "My campaigns" });
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(screen.getByRole("button", { name: /Account menu/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Sign out/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect((await screen.findAllByRole("link", { name: "Sign in" })).length).toBeGreaterThan(0);
    expect(api.called("POST", "/api/auth/logout")).toHaveLength(1);
    expect(api.called("POST", "/api/auth/logout")[0]?.csrf).toBe("test-token");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("shows the demo banner on every shell when the server asks for it", async () => {
    mockApi({
      "GET /api/auth/me": familyUser,
      "GET /api/public/config": { paypal_client_id: "", paypal_mode: "mock", currency: "USD", demo_banner: true, features: { agent: false, appeal_checker: true } },
    });
    renderApp("/app");
    expect(await screen.findByText(/PayPal sandbox money/)).toBeInTheDocument();
  });
});

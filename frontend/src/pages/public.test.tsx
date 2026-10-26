import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "@/test/mockApi";
import { casePage, HOSPITALS, publicCase, STATS } from "@/test/fixtures";
import { renderApp } from "@/test/renderApp";

const anonymous = { "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "x" } } } };

afterEach(() => vi.unstubAllGlobals());

describe("landing page", () => {
  it("explains the product, shows live cases and the figures", async () => {
    mockApi({
      ...anonymous,
      "GET /api/public/cases": casePage([publicCase(), publicCase({ slug: "second", alias: "Amani" })]),
      "GET /api/public/stats": STATS,
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1, name: /Pay the hospital, not a stranger's phone number/ })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Baraka" })).toHaveAttribute("href", "/c/demolive2345");
    expect(screen.getByRole("link", { name: "Amani" })).toBeInTheDocument();
    expect(await screen.findByText("KES 492,885.07")).toBeInTheDocument();
    const steps = screen.getByRole("heading", { name: "How your money reaches the hospital" }).closest("section")!;
    expect(within(steps).getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByRole("link", { name: "Start a campaign" })).toHaveAttribute("href", "/app/new");
  });

  it("offers the appeal checker, and hides it when the feature is off", async () => {
    mockApi({ ...anonymous, "GET /api/public/cases": casePage([]), "GET /api/public/stats": STATS });
    const first = renderApp("/");
    expect(await screen.findByRole("heading", { name: /fundraising message on WhatsApp/ })).toBeInTheDocument();
    first.unmount();
    mockApi({
      ...anonymous,
      "GET /api/public/cases": casePage([]),
      "GET /api/public/stats": STATS,
      "GET /api/public/config": { paypal_client_id: "", paypal_mode: "mock", currency: "USD", demo_banner: false, features: { agent: false, appeal_checker: false } },
    });
    renderApp("/");
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(screen.queryByRole("heading", { name: /fundraising message on WhatsApp/ })).not.toBeInTheDocument());
  });

  it("still works when the live cases and figures cannot be loaded", async () => {
    mockApi({
      ...anonymous,
      "GET /api/public/cases": { status: 500, body: { error: { code: "internal_error", message: "x" } } },
      "GET /api/public/stats": { status: 500, body: { error: { code: "internal_error", message: "x" } } },
    });
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Cases open now" })).not.toBeInTheDocument(), { timeout: 8000 });
  });
});

describe("browse page", () => {
  const listHandlers = (items = [publicCase()], extra = {}) => ({
    ...anonymous,
    "GET /api/hospitals": HOSPITALS,
    "GET /api/public/cases": casePage(items, extra),
  });

  it("lists cases as cards", async () => {
    mockApi(listHandlers([publicCase(), publicCase({ slug: "two", alias: "Amani" })]));
    renderApp("/cases");
    expect(await screen.findByRole("heading", { level: 1, name: "Cases you can support" })).toBeInTheDocument();
    expect(await screen.findAllByTestId("case-card")).toHaveLength(2);
    expect(screen.getByText("2 cases")).toBeInTheDocument();
  });

  it("filters by county using the counties of the active hospitals, once each", async () => {
    const api = mockApi(listHandlers());
    renderApp("/cases");
    await screen.findAllByTestId("case-card");
    await userEvent.click(screen.getByRole("combobox", { name: "Hospital county" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["All counties", "Kisumu", "Mombasa", "Nairobi"]);
    await userEvent.click(screen.getByRole("option", { name: "Mombasa" }));
    await waitFor(() => expect(api.calls.some((call) => call.path === "/api/public/cases" && call.method === "GET")).toBe(true));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Hospital county" })).toHaveTextContent("Mombasa"));
  });

  it("passes the county from the address to the API", async () => {
    let seen = "";
    mockApi({ ...listHandlers(), "GET /api/public/cases": () => casePage([]) });
    const original = globalThis.fetch;
    globalThis.fetch = (async (input: Request | string, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/api/public/cases")) seen = url;
      return original(input as never, init);
    }) as typeof fetch;
    renderApp("/cases?county=Kisumu");
    expect(await screen.findByText("No open cases in Kisumu")).toBeInTheDocument();
    expect(seen).toContain("county=Kisumu");
    expect(screen.getByRole("button", { name: "All counties" })).toBeInTheDocument();
  });

  it("says so when there are no cases at all", async () => {
    mockApi(listHandlers([]));
    renderApp("/cases");
    expect(await screen.findByText("No open cases right now")).toBeInTheDocument();
  });

  it("pages through a long list and keeps the page in the address", async () => {
    mockApi(listHandlers([publicCase()], { total: 30, page_size: 12 }));
    const { router } = renderApp("/cases");
    await screen.findAllByTestId("case-card");
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(router.state.location.search).toBe("?page=2"));
  });

  it("offers a retry when the list cannot be loaded", async () => {
    mockApi({ ...anonymous, "GET /api/hospitals": HOSPITALS, "GET /api/public/cases": { status: 429, headers: { "Retry-After": "12" }, body: { error: { code: "rate_limited", message: "x" } } } });
    renderApp("/cases");
    expect(await screen.findByText("Too many tries. Please wait 12 seconds and try again.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("how it works", () => {
  it("lays out the trust part and the money part, what is simulated, and the leftover choices", async () => {
    mockApi(anonymous);
    renderApp("/how-it-works");
    expect(await screen.findByRole("heading", { level: 1, name: "How TibaDirect works" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Is the bill real?" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Does the money reach the hospital?" })).toBeInTheDocument();
    for (const name of ["Refund me", "Solidarity pool", "Keep for follow-up care"]) expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Simulated in this demo/ })).toBeInTheDocument();
    expect(screen.getByText(/every donor is refunded, whatever they chose/)).toBeInTheDocument();
  });

  it("answers questions in expandable items", async () => {
    mockApi(anonymous);
    renderApp("/how-it-works");
    const question = await screen.findByText("Does TibaDirect hold my money?");
    const details = question.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    await userEvent.click(question);
    expect(details).toHaveAttribute("open");
  });
});

describe("legal pages and not found", () => {
  it("shows the privacy and terms summaries, marked as drafts", async () => {
    mockApi(anonymous);
    const first = renderApp("/privacy");
    expect(await screen.findByRole("heading", { level: 1, name: "Privacy" })).toBeInTheDocument();
    expect(screen.getByText(/full notice will be published after legal review/)).toBeInTheDocument();
    first.unmount();
    renderApp("/terms");
    expect(await screen.findByRole("heading", { level: 1, name: "Terms of use" })).toBeInTheDocument();
  });

  it("shows a friendly page for an unknown address", async () => {
    mockApi(anonymous);
    renderApp("/no-such-page");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(async () => {
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

const anonymous = { "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "x" } } } };

describe("keyboard use", () => {
  it("makes the skip link the first stop, pointing at the main content", async () => {
    mockApi({ ...anonymous });
    renderApp("/how-it-works");
    await screen.findByRole("heading", { level: 1, name: "How TibaDirect works" });
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to main content" });
    expect(skip).toHaveFocus();
    expect(skip).toHaveAttribute("href", "#main");
    expect(document.getElementById("main")).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus to the page content after following a link, so a keyboard user starts at the top of the new page", async () => {
    mockApi({ ...anonymous });
    renderApp("/how-it-works");
    await screen.findByRole("heading", { level: 1 });
    await userEvent.click(screen.getAllByRole("link", { name: "Check an appeal" })[0]!);
    await screen.findByRole("heading", { level: 1, name: "Check an appeal" });
    await vi.waitFor(() => expect(document.activeElement?.id).toBe("main"));
  });

  it("labels every control by its words in Swahili too", async () => {
    await i18n.changeLanguage("sw");
    mockApi({ ...anonymous });
    renderApp("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Ingia" })).toBeInTheDocument();
    expect(screen.getByLabelText("Anwani ya barua pepe")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nitumie msimbo" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ruka hadi maudhui makuu" })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("sw");
  });
});

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { publicCase } from "@/test/fixtures";
import { CaseCard } from "./CaseCard";

const show = (overrides = {}) =>
  render(
    <MemoryRouter>
      <CaseCard item={publicCase(overrides)} />
    </MemoryRouter>,
  );

describe("CaseCard", () => {
  it("links the whole card to the case page with one link", () => {
    show();
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/c/demolive2345");
    expect(links[0]).toHaveTextContent("Baraka");
  });

  it("shows where, which hospital, how many donors and the trust level", () => {
    show();
    expect(screen.getByText("Nairobi")).toBeInTheDocument();
    expect(screen.getByText("Kilele Mission Hospital")).toBeInTheDocument();
    expect(screen.getByText("6 donors")).toBeInTheDocument();
    expect(screen.getByText("Hospital-confirmed")).toBeInTheDocument();
  });

  it("uses the singular for one donor and says when a case is fully funded", () => {
    show({ donor_count: 1, status: "funded" });
    expect(screen.getByText("1 donor")).toBeInTheDocument();
    expect(screen.getByText("Fully funded")).toBeInTheDocument();
  });

  it("shows the bill meter from the case's amounts", () => {
    show();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "30");
  });
});

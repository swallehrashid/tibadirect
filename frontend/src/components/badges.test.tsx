import { render, screen } from "@testing-library/react";
import { RiskBand } from "./RiskBand";
import { TrustBadge } from "./TrustBadge";

describe("TrustBadge", () => {
  it.each([
    ["hospital_confirmed", "Hospital-confirmed", /billing office confirmed/i],
    ["pending_confirmation", "Pending confirmation", /waiting for the hospital to confirm/i],
    ["unverified", "Unverified", /not been checked by the hospital/i],
  ] as const)("says what %s means in words", (level, label, explanation) => {
    render(<TrustBadge level={level} explain />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(explanation)).toBeInTheDocument();
  });

  it("keeps the explanation available to screen readers when it is not shown", () => {
    render(<TrustBadge level="hospital_confirmed" />);
    expect(screen.getByText(/billing office confirmed/i)).toHaveClass("sr-only");
  });
});

describe("RiskBand", () => {
  it.each([
    ["low", "Low concern"],
    ["medium", "Needs a closer look"],
    ["high", "High concern"],
  ] as const)("shows %s in words", (band, text) => {
    render(<RiskBand band={band} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { BillMeter } from "./BillMeter";

const widths = (container: HTMLElement) => ({
  pledged: (container.querySelector('[data-layer="pledged"]') as HTMLElement).style.width,
  settled: (container.querySelector('[data-layer="settled"]') as HTMLElement).style.width,
});

describe("BillMeter", () => {
  it("draws pledged behind settled as shares of the target", () => {
    const { container } = render(<BillMeter targetMinor={10_000_000} pledgedMinor={6_000_000} settledMinor={2_500_000} />);
    expect(widths(container)).toEqual({ pledged: "60%", settled: "25%" });
  });

  it("never draws more than the whole bar", () => {
    const { container } = render(<BillMeter targetMinor={1_000_000} pledgedMinor={1_500_000} settledMinor={1_000_000} />);
    expect(widths(container)).toEqual({ pledged: "100%", settled: "100%" });
  });

  it("never shows settled longer than pledged", () => {
    const { container } = render(<BillMeter targetMinor={1_000_000} pledgedMinor={200_000} settledMinor={900_000} />);
    expect(widths(container).settled).toBe("20%");
  });

  it("describes the amounts in words for assistive technology", () => {
    render(<BillMeter targetMinor={10_000_000} pledgedMinor={6_000_000} settledMinor={2_500_000} />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "60");
    expect(meter).toHaveAttribute("aria-valuetext", "KES 60,000 pledged and KES 25,000 paid to the hospital, out of KES 100,000.");
  });

  it("explains that the amount is not confirmed yet when there is no target", () => {
    const { container } = render(<BillMeter targetMinor={null} pledgedMinor={0} settledMinor={0} />);
    expect(screen.getByText(/once the hospital confirms the bill/i)).toBeInTheDocument();
    expect(widths(container)).toEqual({ pledged: "0%", settled: "0%" });
  });

  it("labels an estimate as an estimate and shows the dollar goal", () => {
    render(<BillMeter targetMinor={5_000_000} pledgedMinor={0} settledMinor={0} isEstimate usdGoalMinor={42_000} />);
    expect(screen.getByText("Estimated bill")).toBeInTheDocument();
    expect(screen.getByText("About $420 to give")).toBeInTheDocument();
  });
});

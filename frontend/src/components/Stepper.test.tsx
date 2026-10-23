import { render, screen } from "@testing-library/react";
import { Stepper } from "./Stepper";

const STEPS = ["Hospital", "Consent", "Bill", "Page", "Review"];

describe("Stepper", () => {
  it("marks only the current step and says where the person is", () => {
    render(<Stepper steps={STEPS} current={2} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("aria-current"))).toEqual([null, null, "step", null, null]);
    expect(screen.getByText("Step 3 of 5: Bill")).toBeInTheDocument();
  });

  it("tells screen readers which steps are done", () => {
    render(<Stepper steps={STEPS} current={2} />);
    expect(screen.getAllByText("(Done)")).toHaveLength(2);
  });
});

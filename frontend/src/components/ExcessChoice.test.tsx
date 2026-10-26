import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ExcessChoice } from "./ExcessChoice";

describe("ExcessChoice", () => {
  it("offers the three choices with a plain-language explanation each", () => {
    render(<ExcessChoice value="refund" onChange={() => {}} />);
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    expect(screen.getByText(/goes back to you through PayPal/)).toBeInTheDocument();
    expect(screen.getByText(/shared pool, written in the public ledger/)).toBeInTheDocument();
    expect(screen.getByText(/Held for up to 14 days/)).toBeInTheDocument();
  });

  it("marks the current choice and reports a change", async () => {
    const onChange = vi.fn();
    render(<ExcessChoice value="refund" onChange={onChange} />);
    expect(screen.getByRole("radio", { name: /Refund me/ })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: /Solidarity pool/ }));
    expect(onChange).toHaveBeenCalledWith("solidarity_pool");
  });

  it("cannot be changed while disabled", async () => {
    const onChange = vi.fn();
    render(<ExcessChoice value="refund" onChange={onChange} disabled />);
    await userEvent.click(screen.getByRole("radio", { name: /Keep for follow-up care/ }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

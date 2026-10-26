import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MoneyInput } from "./MoneyInput";

function Harness({ initial = null as number | null }) {
  const [value, setValue] = useState<number | null>(initial);
  return (
    <>
      <MoneyInput value={value} onChange={setValue} />
      <output data-testid="minor">{value === null ? "none" : value}</output>
      <button onClick={() => setValue(5000)}>preset</button>
    </>
  );
}

describe("MoneyInput", () => {
  it("reports whole and fractional amounts in minor units", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByRole("textbox"), "25.5");
    expect(screen.getByTestId("minor")).toHaveTextContent("2550");
  });

  it("lets a person type a trailing point without losing it", async () => {
    render(<Harness />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "12.");
    expect(input).toHaveValue("12.");
  });

  it("drops letters and symbols and reports no amount for an invalid one", async () => {
    render(<Harness />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "$1a0");
    expect(input).toHaveValue("10");
    await userEvent.type(input, ".999");
    expect(screen.getByTestId("minor")).toHaveTextContent("none");
  });

  it("follows a change made from outside, such as a preset", async () => {
    render(<Harness initial={1000} />);
    expect(screen.getByRole("textbox")).toHaveValue("10");
    await userEvent.click(screen.getByText("preset"));
    expect(screen.getByRole("textbox")).toHaveValue("50");
  });

  it("tidies the text on blur", async () => {
    render(<Harness />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "7.5");
    await userEvent.tab();
    expect(input).toHaveValue("7.50");
  });
});

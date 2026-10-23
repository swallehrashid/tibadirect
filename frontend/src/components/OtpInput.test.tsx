import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { vi } from "vitest";
import { OtpInput } from "./OtpInput";

function Harness({ onComplete }: { onComplete?: (code: string) => void }) {
  const [value, setValue] = useState("");
  return <OtpInput value={value} onChange={setValue} onComplete={onComplete} />;
}

const boxes = () => screen.getAllByRole("textbox") as HTMLInputElement[];
const code = () => boxes().map((box) => box.value).join("");

describe("OtpInput", () => {
  it("moves to the next box as digits are typed and reports the finished code once", async () => {
    const onComplete = vi.fn();
    render(<Harness onComplete={onComplete} />);
    await userEvent.click(boxes()[0]!);
    await userEvent.keyboard("123456");
    expect(code()).toBe("123456");
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith("123456");
  });

  it("ignores letters", async () => {
    render(<Harness />);
    await userEvent.click(boxes()[0]!);
    await userEvent.keyboard("1a2b3");
    expect(code()).toBe("123");
  });

  it("fills every box when a code is pasted", async () => {
    const onComplete = vi.fn();
    render(<Harness onComplete={onComplete} />);
    await userEvent.click(boxes()[0]!);
    await userEvent.paste("482 913");
    expect(code()).toBe("482913");
    expect(onComplete).toHaveBeenCalledWith("482913");
  });

  it("deletes the previous digit when backspace is pressed on an empty box", async () => {
    render(<Harness />);
    await userEvent.click(boxes()[0]!);
    await userEvent.keyboard("12");
    expect(code()).toBe("12");
    await userEvent.keyboard("{Backspace}");
    expect(code()).toBe("1");
    await userEvent.keyboard("{Backspace}");
    expect(code()).toBe("");
  });

  it("labels each box and offers the code to the phone's autofill", () => {
    render(<Harness />);
    expect(boxes()).toHaveLength(6);
    expect(boxes()[0]).toHaveAttribute("autocomplete", "one-time-code");
    expect(screen.getByLabelText("Digit 3 of 6")).toBeInTheDocument();
  });

  it("can be completed again after an edit", async () => {
    const onComplete = vi.fn();
    render(<Harness onComplete={onComplete} />);
    await userEvent.click(boxes()[0]!);
    await userEvent.keyboard("123456{Backspace}7");
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenLastCalledWith("123457");
  });
});

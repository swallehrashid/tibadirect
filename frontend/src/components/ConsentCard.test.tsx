import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ConsentCard } from "./ConsentCard";

const base = { title: "Hospital check", text: "We will ask the hospital to confirm your bill.", required: true, checked: false, onCheckedChange: () => {} };

describe("ConsentCard", () => {
  it("keeps the full wording hidden until asked", async () => {
    render(<ConsentCard {...base} />);
    const toggle = screen.getByRole("button", { name: /read the full wording/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(base.text)).not.toBeVisible();
    await userEvent.click(toggle);
    expect(screen.getByText(base.text)).toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("reports ticking the box", async () => {
    const onCheckedChange = vi.fn();
    render(<ConsentCard {...base} onCheckedChange={onCheckedChange} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /I agree: Hospital check/ }));
    expect(onCheckedChange).toHaveBeenCalledWith(true);
  });

  it("says whether the consent is required, and shows guardian wording for a minor", () => {
    const { rerender } = render(<ConsentCard {...base} guardian />);
    expect(screen.getByText("Required to continue")).toBeInTheDocument();
    expect(screen.getByText(/on behalf of the patient/i)).toBeInTheDocument();
    rerender(<ConsentCard {...base} required={false} />);
    expect(screen.getByText("Optional")).toBeInTheDocument();
  });
});

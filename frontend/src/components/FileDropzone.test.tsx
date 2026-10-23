import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { MAX_UPLOAD_BYTES } from "@/lib/uploads";
import { FileDropzone } from "./FileDropzone";

const input = (container: HTMLElement) => container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;

describe("FileDropzone", () => {
  it("hands over a valid file", async () => {
    const onChange = vi.fn();
    const { container } = render(<FileDropzone file={null} onChange={onChange} />);
    const bill = new File(["%PDF-1.7"], "bill.pdf", { type: "application/pdf" });
    await userEvent.upload(input(container), bill);
    expect(onChange).toHaveBeenCalledWith(bill);
  });

  it("refuses the wrong type with a message and does not hand it over", async () => {
    const onChange = vi.fn();
    const { container } = render(<FileDropzone file={null} onChange={onChange} />);
    // applyAccept is off so the picker's own filter does not hide the case we want to test.
    await userEvent.upload(input(container), new File(["x"], "notes.txt", { type: "text/plain" }), { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("Please choose a PDF, PNG or JPEG file.");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("refuses a file over 10 MB", async () => {
    const onChange = vi.fn();
    const { container } = render(<FileDropzone file={null} onChange={onChange} />);
    const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], "bill.pdf", { type: "application/pdf" });
    await userEvent.upload(input(container), big);
    expect(await screen.findByRole("alert")).toHaveTextContent("larger than 10 MB");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("shows the chosen file and lets it be removed", async () => {
    const onChange = vi.fn();
    const bill = new File(["%PDF-1.7"], "bill.pdf", { type: "application/pdf" });
    render(<FileDropzone file={bill} onChange={onChange} />);
    expect(screen.getByText(/bill\.pdf/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /remove/i }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("shows an error given from outside", () => {
    render(<FileDropzone file={null} onChange={() => {}} error="The server did not accept this file." />);
    expect(screen.getByRole("alert")).toHaveTextContent("The server did not accept this file.");
  });
});

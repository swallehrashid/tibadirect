import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { ApiError } from "@/api/errors";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("quotes the request reference and offers a retry", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(500, { code: "internal_error", message: "boom", request_id: "req-123" })} onRetry={onRetry} />);
    expect(screen.getByText("Reference: req-123")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("tells the person how long to wait when rate limited", () => {
    render(<ErrorState error={new ApiError(429, { code: "rate_limited", message: "x" }, 42)} />);
    expect(screen.getByText("Too many tries. Please wait 42 seconds and try again.")).toBeInTheDocument();
  });

  it("does not offer a retry for something that was not found", () => {
    render(<ErrorState error={new ApiError(404, { code: "not_found", message: "x" })} onRetry={() => {}} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("explains a network failure", () => {
    render(<ErrorState error={new TypeError("Failed to fetch")} />);
    expect(screen.getByText(/could not reach the service/i)).toBeInTheDocument();
  });
});

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CASE_BASE, publicCase, RECEIPT } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const live = (overrides = {}) => ({ ...CASE_BASE, "GET /api/public/cases/demolive2345": publicCase(overrides) });
const orderOk = { status: 201, body: { order_id: "ord-1", amount_usd_minor: 2500, amount_usd: "25.00" } };
const captureOk = { body: { receipt_token: "tok123", donation: RECEIPT } };

describe("case page", () => {
  it("shows who, where, the trust badge with its explanation, the meter, the story and the payments so far", async () => {
    mockApi(live());
    renderApp("/c/demolive2345");
    expect(await screen.findByRole("heading", { level: 1, name: "Baraka" })).toBeInTheDocument();
    expect(screen.getByText("Kilele Mission Hospital")).toBeInTheDocument();
    expect(screen.getByText("Hospital-confirmed")).toBeInTheDocument();
    expect(screen.getByText(/billing office confirmed this bill/)).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "30");
    expect(screen.getByText(/Every shilling goes to the hospital/)).toBeInTheDocument();
    expect(screen.getByText(/No diagnosis is shown/)).toBeInTheDocument();
    expect(await screen.findByText("Payment 1: KES 67,445.07")).toBeInTheDocument();
    expect(screen.getByText(/no more than 80% of it is paid/)).toBeInTheDocument();
  });

  it("shows the public ledger with donor names only where given and a verify button", async () => {
    mockApi(live());
    renderApp("/c/demolive2345");
    expect(await screen.findByRole("heading", { name: "Public ledger" })).toBeInTheDocument();
    const grid = await screen.findByTestId("ledger-grid");
    expect(await within(grid).findByText("Donation received from Wanjiru K.")).toBeInTheDocument();
    expect(within(grid).getByText("PayPal fee")).toBeInTheDocument();
    expect(within(grid).getByText("+KES 67,445.07")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify ledger" })).toBeInTheDocument();
  });

  it("verifies the ledger on request and reports an intact chain", async () => {
    const api = mockApi({
      ...live(),
      "GET /api/public/cases/demolive2345/ledger/verify": { valid: true, entries_checked: 3, last_hash: "54fa6ee803da9999", checked_at: "2026-10-23T10:00:00+00:00" },
    });
    renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: "Verify ledger" }));
    expect(await screen.findByText("The ledger is intact")).toBeInTheDocument();
    expect(screen.getByText(/all 3 entries.*54fa6ee803da/)).toBeInTheDocument();
    expect(api.called("GET", "/api/public/cases/demolive2345/ledger/verify")).toHaveLength(1);
  });

  it("warns when the chain does not match", async () => {
    mockApi({ ...live(), "GET /api/public/cases/demolive2345/ledger/verify": { valid: false, entries_checked: 3, last_hash: "00", checked_at: "2026-10-23T10:00:00+00:00" } });
    renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: "Verify ledger" }));
    expect(await screen.findByText("The ledger check failed")).toBeInTheDocument();
  });

  it("does not check the ledger until asked", async () => {
    const api = mockApi({ ...live(), "GET /api/public/cases/demolive2345/ledger/verify": { valid: true, entries_checked: 3, last_hash: "ab", checked_at: "x" } });
    renderApp("/c/demolive2345");
    await screen.findByRole("heading", { name: "Public ledger" });
    expect(api.called("GET", "/api/public/cases/demolive2345/ledger/verify")).toHaveLength(0);
  });

  it("says a case is not found when it is private, paused or wrong", async () => {
    mockApi({ ...CASE_BASE, "GET /api/public/cases/demolive2345": { status: 404, body: { error: { code: "case_not_found", message: "Case not found." } } } });
    renderApp("/c/demolive2345");
    expect(await screen.findByText("We could not find that case")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Donate/ })).not.toBeInTheDocument();
  });

  it("closes the donation form for a fully funded case", async () => {
    mockApi(live({ status: "funded" }));
    renderApp("/c/demolive2345");
    expect(await screen.findByText("This case is fully funded")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Donate/ })).not.toBeInTheDocument();
  });

  it("closes the donation form for a closed case and keeps the page as a record", async () => {
    mockApi(live({ status: "closed" }));
    renderApp("/c/demolive2345");
    expect(await screen.findByText("This case is closed")).toBeInTheDocument();
    expect(screen.getByText(/stays as a public record/)).toBeInTheDocument();
  });
});

describe("donating (simulated PayPal)", () => {
  it("starts at $25 and shows the shilling estimate, updating with the amount", async () => {
    mockApi(live());
    renderApp("/c/demolive2345");
    expect(await screen.findByText(/About KES 3,225 at today's rate/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "$25" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "$50" }));
    expect(await screen.findByText(/About KES 6,450 at today's rate/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "$50" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "$25" })).toHaveAttribute("aria-pressed", "false");
  });

  it("will not pay for an amount outside $1 to $10,000", async () => {
    mockApi(live());
    renderApp("/c/demolive2345");
    const input = await screen.findByLabelText("Amount in US dollars");
    await userEvent.clear(input);
    await userEvent.type(input, "0.50");
    expect(screen.getByText("Enter an amount between $1 and $10,000.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Donate/ })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "10001");
    expect(screen.getByRole("button", { name: /Donate/ })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "10000");
    expect(screen.getByRole("button", { name: /Donate/ })).toBeEnabled();
  });

  it("creates the order, captures it, and opens the receipt", async () => {
    const api = mockApi({
      ...live(),
      "POST /api/donations/orders": orderOk,
      "POST /api/donations/orders/ord-1/capture": captureOk,
      "GET /api/donations/receipts/tok123": RECEIPT,
    });
    const { router } = renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: /Donate \$25 with PayPal \(simulated\)/ }));
    expect(await screen.findByRole("heading", { name: "Thank you" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/receipt/tok123");
    expect(api.called("POST", "/api/donations/orders")[0]?.body).toEqual({
      case_slug: "demolive2345",
      amount_usd: "25.00",
      excess_choice: "refund",
      donor_display_name: null,
      is_anonymous: true,
    });
    expect(api.called("POST", "/api/donations/orders")[0]?.csrf).toBe("test-token");
    expect(api.called("POST", "/api/donations/orders/ord-1/capture")).toHaveLength(1);
    expect(screen.getByText("$23.95")).toBeInTheDocument();
  });

  it("sends the chosen amount, name and leftover choice", async () => {
    const api = mockApi({ ...live(), "POST /api/donations/orders": orderOk, "POST /api/donations/orders/ord-1/capture": captureOk, "GET /api/donations/receipts/tok123": RECEIPT });
    renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: "$100" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Give anonymously" }));
    await userEvent.type(screen.getByLabelText("Name shown in the public ledger (optional)"), "  Amina  ");
    await userEvent.click(screen.getByRole("radio", { name: /Keep for follow-up care/ }));
    await userEvent.click(screen.getByRole("button", { name: /Donate \$100/ }));
    await screen.findByRole("heading", { name: "Thank you" });
    expect(api.called("POST", "/api/donations/orders")[0]?.body).toEqual({
      case_slug: "demolive2345",
      amount_usd: "100.00",
      excess_choice: "follow_up_care",
      donor_display_name: "Amina",
      is_anonymous: false,
    });
  });

  it("treats a name left empty as anonymous", async () => {
    const api = mockApi({ ...live(), "POST /api/donations/orders": orderOk, "POST /api/donations/orders/ord-1/capture": captureOk, "GET /api/donations/receipts/tok123": RECEIPT });
    renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Give anonymously" }));
    await userEvent.click(screen.getByRole("button", { name: /Donate \$25/ }));
    await screen.findByRole("heading", { name: "Thank you" });
    expect(api.called("POST", "/api/donations/orders")[0]?.body).toMatchObject({ donor_display_name: null, is_anonymous: true });
  });

  it("tells the donor they were not charged when the case filled up before the capture", async () => {
    const api = mockApi({
      ...live(),
      "POST /api/donations/orders": orderOk,
      "POST /api/donations/orders/ord-1/capture": { status: 409, body: { error: { code: "case_fully_funded", message: "Funded." } } },
    });
    const { router } = renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: /Donate \$25/ }));
    expect(await screen.findByText(/no longer needs more donations. You have not been charged/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/c/demolive2345");
    expect(screen.getByRole("button", { name: /Donate \$25/ })).toBeEnabled();
    expect(api.called("POST", "/api/donations/orders/ord-1/capture")).toHaveLength(1);
  });

  it("explains when the case stopped taking donations before the order was made", async () => {
    mockApi({ ...live(), "POST /api/donations/orders": { status: 409, body: { error: { code: "case_not_accepting", message: "x" } } } });
    renderApp("/c/demolive2345");
    await userEvent.click(await screen.findByRole("button", { name: /Donate \$25/ }));
    expect(await screen.findByText(/not taking donations right now/)).toBeInTheDocument();
  });

  it("labels the payment as simulated", async () => {
    mockApi(live());
    renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: /Donate/ });
    expect(screen.getAllByText("Sandbox / simulated").length).toBeGreaterThan(0);
  });
});

describe("receipt page", () => {
  it("shows what was paid, the fee, what reaches the case, the shilling value and the ledger fingerprint", async () => {
    mockApi({ ...CASE_BASE, "GET /api/donations/receipts/tok123": RECEIPT });
    renderApp("/receipt/tok123");
    expect(await screen.findByRole("heading", { name: "Thank you" })).toBeInTheDocument();
    const list = document.querySelector("dl") as HTMLElement;
    expect(within(list).getByText("$25")).toBeInTheDocument();
    expect(within(list).getByText("$1.05")).toBeInTheDocument();
    expect(within(list).getByText("$23.95")).toBeInTheDocument();
    expect(within(list).getByText(/KES 3,089.55 \(1 USD = 129.00 KES\)/)).toBeInTheDocument();
    expect(within(list).getByText("Refund me")).toBeInTheDocument();
    expect(within(list).getByText("dfe246cc0754")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See it in the public ledger" })).toHaveAttribute("href", "/c/demolive2345#ledger");
  });

  it("says when a donation has been refunded", async () => {
    mockApi({ ...CASE_BASE, "GET /api/donations/receipts/tok123": { ...RECEIPT, status: "partially_refunded", refunded_usd_minor: 500 } });
    renderApp("/receipt/tok123");
    expect(await screen.findByText("$5 of this donation has been refunded.")).toBeInTheDocument();
  });

  it("shows a friendly message for an unknown receipt link", async () => {
    mockApi({ ...CASE_BASE, "GET /api/donations/receipts/nope": { status: 404, body: { error: { code: "receipt_not_found", message: "x" } } } });
    renderApp("/receipt/nope");
    expect(await screen.findByText("We could not find that receipt")).toBeInTheDocument();
  });
});

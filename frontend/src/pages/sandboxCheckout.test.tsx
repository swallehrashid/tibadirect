import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CASE_BASE, publicCase, RECEIPT } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

// PayPal's v6 button needs the real SDK from paypal.com, so the test stands a fake in its place and
// calls the same callbacks the SDK would call.
interface ButtonProps {
  disabled?: boolean;
  type?: string;
  createOrder: () => Promise<{ orderId: string }>;
  onApprove: (data: { orderId: string }) => Promise<void>;
  onCancel: () => void;
  onError: (error: unknown) => void;
}
const sdk = vi.hoisted(() => ({ button: null as null | { current: unknown }, provider: null as null | { current: unknown } }));

vi.mock("@paypal/react-paypal-js/sdk-v6", () => ({
  PayPalProvider: (props: Record<string, unknown> & { children: React.ReactNode }) => {
    sdk.provider = { current: props };
    return <div data-testid="paypal-provider">{props.children}</div>;
  },
  PayPalOneTimePaymentButton: (props: ButtonProps) => {
    sdk.button = { current: props };
    return <button disabled={props.disabled}>PayPal donate button</button>;
  },
}));

const button = () => sdk.button!.current as ButtonProps;
const sandboxConfig = { paypal_client_id: "sandbox-client-id", paypal_mode: "sandbox", currency: "USD", demo_banner: true, features: { agent: false, appeal_checker: true } };

beforeEach(() => {
  sdk.button = null;
  sdk.provider = null;
});
afterEach(() => vi.unstubAllGlobals());

function setup(extra: Record<string, unknown> = {}) {
  return mockApi({
    ...CASE_BASE,
    "GET /api/public/config": sandboxConfig,
    "GET /api/public/cases/demolive2345": publicCase(),
    "POST /api/donations/orders": { status: 201, body: { order_id: "ord-9", amount_usd_minor: 2500, amount_usd: "25.00" } },
    "POST /api/donations/orders/ord-9/capture": { body: { receipt_token: "tok123", donation: RECEIPT } },
    "GET /api/donations/receipts/tok123": RECEIPT,
    ...extra,
  });
}

describe("donating with the PayPal sandbox button", () => {
  it("loads PayPal's SDK for the sandbox with the public client ID and a donate button", async () => {
    setup();
    renderApp("/c/demolive2345");
    expect(await screen.findByRole("button", { name: "PayPal donate button" })).toBeEnabled();
    expect(sdk.provider!.current).toMatchObject({ clientId: "sandbox-client-id", environment: "sandbox", components: ["paypal-payments"] });
    expect(button().type).toBe("donate");
    expect(screen.queryByRole("button", { name: /simulated/ })).not.toBeInTheDocument();
  });

  it("creates the order on our server from the form as it stands when PayPal asks", async () => {
    const api = setup();
    renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: "PayPal donate button" });
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(screen.getByRole("button", { name: "$50" })); // changed after the button was first drawn
    const order = await button().createOrder();
    expect(order).toEqual({ orderId: "ord-9" });
    expect(api.called("POST", "/api/donations/orders")[0]?.body).toMatchObject({ amount_usd: "50.00", excess_choice: "refund" });
  });

  it("captures on our server after approval and opens the receipt", async () => {
    const api = setup();
    const { router } = renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: "PayPal donate button" });
    await act(async () => {
      await button().onApprove({ orderId: "ord-9" });
    });
    expect(api.called("POST", "/api/donations/orders/ord-9/capture")).toHaveLength(1);
    await waitFor(() => expect(router.state.location.pathname).toBe("/receipt/tok123"));
  });

  it("explains a refused capture and does not open a receipt", async () => {
    setup({ "POST /api/donations/orders/ord-9/capture": { status: 409, body: { error: { code: "case_fully_funded", message: "x" } } } });
    const { router } = renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: "PayPal donate button" });
    await act(async () => {
      await button().onApprove({ orderId: "ord-9" });
    });
    expect(await screen.findByText(/You have not been charged/)).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/c/demolive2345");
  });

  it("clears the message when the donor cancels in PayPal's window", async () => {
    setup({ "POST /api/donations/orders/ord-9/capture": { status: 422, body: { error: { code: "payment_declined", message: "x" } } } });
    renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: "PayPal donate button" });
    await act(async () => {
      await button().onApprove({ orderId: "ord-9" });
    });
    expect(await screen.findByText(/did not complete this payment/)).toBeInTheDocument();
    act(() => button().onCancel());
    await waitFor(() => expect(screen.queryByText(/did not complete this payment/)).not.toBeInTheDocument());
  });

  it("disables PayPal's button while the amount is not valid", async () => {
    setup();
    renderApp("/c/demolive2345");
    await screen.findByRole("button", { name: "PayPal donate button" });
    const { default: userEvent } = await import("@testing-library/user-event");
    const input = screen.getByLabelText("Amount in US dollars");
    await userEvent.clear(input);
    expect(screen.getByRole("button", { name: "PayPal donate button" })).toBeDisabled();
  });
});

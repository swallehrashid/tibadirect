import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { CASE_ID, FAMILY_BASE, familyCase, MONEY_ZERO } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(async () => {
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

const path = `/api/cases/${CASE_ID}`;
const triage = { risk_band: "low", route: "hospital_confirmation", reasons_en: ["The amounts add up."], reasons_sw: ["Viwango vinajumlishana."], created_at: "2026-10-23T08:05:00+00:00" };
const timeline = (...statuses: string[]) => statuses.map((status, index) => ({ status, at: `2026-10-23T0${8 + index}:00:00+00:00` }));
const setup = (overrides: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  mockApi({ ...FAMILY_BASE, [`GET ${path}`]: familyCase(overrides), ...extra });
const live = { status: "live", trust_level: "hospital_confirmed", is_publicly_visible: true, consents: ["hospital_verification", "public_page"], timeline: timeline("draft", "pending_triage", "pending_confirmation", "live"), triage };

describe("case dashboard", () => {
  it("shows where the case is, what to do next, the money and what the checks found", async () => {
    setup({
      status: "pending_confirmation",
      trust_level: "pending_confirmation",
      consents: ["hospital_verification", "public_page"],
      timeline: timeline("draft", "pending_triage", "pending_confirmation"),
      triage,
    });
    renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Zawadi" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for the hospital to confirm your bill.")).toBeInTheDocument();
    const steps = screen.getByRole("heading", { name: "Where your case is" }).closest("div[class*=rounded]")!;
    expect(within(steps as HTMLElement).getAllByRole("listitem").map((item) => item.getAttribute("data-state"))).toEqual(["done", "done", "current", "upcoming", "upcoming", "upcoming"]);
    expect(screen.getByText("The amounts add up.")).toBeInTheDocument();
    expect(screen.getByText("Low concern")).toBeInTheDocument();
    expect(screen.getByText("Sent to the hospital's billing office to confirm.")).toBeInTheDocument();
    expect(screen.getByText(/The amount will show here once the hospital confirms the bill/)).toBeInTheDocument();
  });

  it("shows the reasons in Swahili when the language is Swahili", async () => {
    await i18n.changeLanguage("sw");
    setup({ status: "pending_confirmation", timeline: timeline("draft", "pending_triage", "pending_confirmation"), triage });
    renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByText("Viwango vinajumlishana.")).toBeInTheDocument();
    expect(screen.queryByText("The amounts add up.")).not.toBeInTheDocument();
  });

  it("lets a family ask for a review and says when they already did", async () => {
    let calls = 0;
    const api = setup(
      { status: "pending_confirmation", timeline: timeline("draft", "pending_triage", "pending_confirmation"), triage },
      {
        [`POST ${path}/review-request`]: () => (calls++ === 0 ? { status: 201, body: { id: "r1", status: "open", created_at: "x" } } : { status: 409, body: { error: { code: "review_already_requested", message: "x" } } }),
      },
    );
    renderApp(`/app/cases/${CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Ask for a review" }));
    const dialog = await screen.findByRole("dialog");
    const send = within(dialog).getByRole("button", { name: "Send request" });
    expect(send).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Your message"), "Please look at the total again.");
    await userEvent.click(send);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.called("POST", `${path}/review-request`)[0]?.body).toEqual({ message: "Please look at the total again." });

    await userEvent.click(screen.getByRole("button", { name: "Ask for a review" }));
    const again = await screen.findByRole("dialog");
    await userEvent.type(within(again).getByLabelText("Your message"), "Please look at it once more.");
    await userEvent.click(within(again).getByRole("button", { name: "Send request" }));
    expect(await within(again).findByText("You already asked for a review. We will look at it soon.")).toBeInTheDocument();
  });

  it("shows what the hospital said, including its question", async () => {
    setup({
      status: "pending_confirmation",
      timeline: timeline("draft", "pending_triage", "pending_confirmation"),
      confirmations: [{ status: "queried", amount_kes_minor: null, invoice_number: "KMH/26/1", is_final_bill: false, query_note: "The total does not match the lines.", created_at: "2026-10-23T09:00:00+00:00", finalized_at: null }],
    });
    renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByText("The hospital has a question")).toBeInTheDocument();
    expect(screen.getByText("The total does not match the lines.")).toBeInTheDocument();
    expect(screen.getByText("Invoice KMH/26/1")).toBeInTheDocument();
  });

  it("for a live case shows the public page link, the money, and the final bill upload", async () => {
    const api = setup(
      { ...live, money: { ...MONEY_ZERO, target_kes_minor: 24_000_000, pledged_kes_minor: 7_204_134, settled_kes_minor: 6_744_507, captured_net_usd_minor: 55_500, donor_count: 6, is_estimate: true } },
      { [`POST ${path}/final-bill`]: { status: 201, body: { id: "d2", kind: "final_bill", status: "uploaded", mime_type: "application/pdf", size_bytes: 10, page_count: 1, created_at: "x", raw_available: true, preview_url: null } } },
    );
    const { container } = renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByRole("link", { name: /View your page/ })).toHaveAttribute("href", "/c/zawadi01");
    expect(screen.getByText(/\/c\/zawadi01$/)).toBeInTheDocument();
    expect(screen.getByText("$555")).toBeInTheDocument();
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "30");
    expect(screen.getByText(/Money has been pledged and is being paid to the hospital/)).toBeInTheDocument();
    await userEvent.upload(container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement, new File(["%PDF-1.7"], "final.pdf", { type: "application/pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Send final bill" }));
    await waitFor(() => expect(api.called("POST", `${path}/final-bill`)).toHaveLength(1));
    expect(await screen.findByText("Your final bill is being checked and sent to the hospital.", { selector: "p" })).toBeInTheDocument();
  });

  it("offers the final bill while the case is live or already fully funded, and only on an estimate", async () => {
    const first = setup({ status: "funded", trust_level: "hospital_confirmed", timeline: timeline("draft", "pending_triage", "pending_confirmation", "live", "funded") });
    const funded = renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByRole("heading", { name: "Upload the final bill" })).toBeInTheDocument();
    funded.unmount();
    void first;

    setup({ ...live, money: { ...MONEY_ZERO, target_kes_minor: 24_000_000, is_estimate: false } });
    const final = renderApp(`/app/cases/${CASE_ID}`);
    await screen.findByRole("heading", { level: 1, name: "Zawadi" });
    expect(screen.queryByRole("heading", { name: "Upload the final bill" })).not.toBeInTheDocument();
    final.unmount();

    setup({ status: "pending_confirmation", trust_level: "pending_confirmation", timeline: timeline("draft", "pending_triage", "pending_confirmation") });
    renderApp(`/app/cases/${CASE_ID}`);
    await screen.findByRole("heading", { level: 1, name: "Zawadi" });
    expect(screen.queryByRole("heading", { name: "Upload the final bill" })).not.toBeInTheDocument();
  });

  it("explains what withdrawing each consent would change before doing it", async () => {
    const api = setup(live, { [`DELETE ${path}/consents/public_page`]: { body: { id: "c", scope: "public_page", actor: "self", wording_version: "v1", language: "en", granted_at: "x", withdrawn_at: "y", active: false } } });
    renderApp(`/app/cases/${CASE_ID}`);
    const withdraw = await screen.findByRole("button", { name: /Withdraw: Show this case on a public page/ });
    await userEvent.click(withdraw);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Your page is hidden straight away and people can no longer give.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw" }));
    await waitFor(() => expect(api.called("DELETE", `${path}/consents/public_page`)).toHaveLength(1));
    expect(api.called("DELETE", `${path}/consents/public_page`)[0]?.csrf).toBe("test-token");
  });

  it("warns that withdrawing the hospital check before confirmation ends the campaign", async () => {
    setup({ status: "pending_confirmation", trust_level: "pending_confirmation", consents: ["hospital_verification", "public_page"], timeline: timeline("draft", "pending_triage", "pending_confirmation") });
    renderApp(`/app/cases/${CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Withdraw: Let the hospital confirm the bill/ }));
    expect(within(await screen.findByRole("alertdialog")).getByText(/If you withdraw this, your campaign ends/)).toBeInTheDocument();
  });

  it("ends a live campaign with the reason chosen", async () => {
    const api = setup(live, { [`POST ${path}/cancel`]: familyCase({ status: "cancelled" }) });
    renderApp(`/app/cases/${CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "End campaign" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/Donations stop at once/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("radio", { name: "The patient has died" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "End campaign" }));
    await waitFor(() => expect(api.called("POST", `${path}/cancel`)).toHaveLength(1));
    expect(api.called("POST", `${path}/cancel`)[0]?.body).toEqual({ reason: "patient_deceased" });
  });

  it("deletes a draft and goes back to the list", async () => {
    const api = setup({}, { [`DELETE ${path}`]: { status: 204 } });
    const { router } = renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByRole("link", { name: /Continue your campaign/ })).toHaveAttribute("href", `/app/new?case=${CASE_ID}&step=2`);
    await userEvent.click(screen.getByRole("button", { name: "Delete draft" }));
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete draft" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
    expect(api.called("DELETE", path)).toHaveLength(1);
  });

  it("tells a family when their page is paused and offers no way to end it", async () => {
    setup({ status: "frozen", timeline: timeline("draft", "pending_triage", "pending_confirmation", "live", "frozen") });
    renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByText(/Your page is paused while we check something/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End campaign" })).not.toBeInTheDocument();
  });

  it("says when a case belongs to someone else or does not exist", async () => {
    mockApi({ ...FAMILY_BASE, [`GET ${path}`]: { status: 404, body: { error: { code: "case_not_found", message: "x" } } } });
    renderApp(`/app/cases/${CASE_ID}`);
    expect(await screen.findByText("We could not find that campaign")).toBeInTheDocument();
  });
});

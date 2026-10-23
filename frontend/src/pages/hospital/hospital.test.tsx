import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CLERK, confirmationRecord, HOSPITAL_CASE_ID, hospitalCase, HPATH, SUPERVISOR } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const otpSent = { status: 201, body: { request_id: "88888888-8888-4888-8888-888888888888", destination_masked: "bil***@kilele-hospital.test", expires_at: "2026-10-23T13:46:00+00:00" } };
const decided = (status: string, caseStatus: string) => ({ body: { confirmation: confirmationRecord({ status }), case_status: caseStatus } });

async function typeCode(code: string) {
  await userEvent.click(screen.getByRole("textbox", { name: /Digit 1 of 6/ }));
  await userEvent.keyboard(code);
}

describe("hospital queue", () => {
  const queue = {
    pending: [
      { id: HOSPITAL_CASE_ID, alias: "Zawadi", region: "Nairobi", submitted_at: "2026-10-23T08:30:00+00:00", risk_band: "medium", bills: 2, kind: "first_bill" },
      { id: "99999999-9999-4999-8999-999999999999", alias: "Baraka", region: "Nairobi", submitted_at: null, risk_band: "low", bills: 1, kind: "final_bill" },
    ],
    awaiting_second_approval: [{ ...confirmationRecord(), case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", case_alias: "Neema" }],
  };

  it("lists bills waiting for a decision with their risk band, and bills waiting for a second approval", async () => {
    mockApi({ "GET /api/auth/me": SUPERVISOR, "GET /api/hospital/queue": queue });
    renderApp("/hospital");
    expect(await screen.findByRole("heading", { name: /Bills waiting for you/ })).toBeInTheDocument();
    const pending = await screen.findAllByTestId("queue-pending");
    expect(pending).toHaveLength(2);
    expect(within(pending[0]!).getByText("Needs a closer look")).toBeInTheDocument();
    expect(within(pending[0]!).getByText("2 bills")).toBeInTheDocument();
    expect(within(pending[1]!).getByText("Final bill")).toBeInTheDocument();
    expect(within(pending[0]!).getByRole("link", { name: /Review/ })).toHaveAttribute("href", `/hospital/cases/${HOSPITAL_CASE_ID}`);
    const second = screen.getByTestId("queue-second");
    expect(within(second).getByRole("heading", { name: "Neema" })).toBeInTheDocument();
    expect(within(second).getByText(/Confirmed by Kilele Mission Hospital clerk/)).toBeInTheDocument();
  });

  it("offers a supervisor the approval link, and tells a clerk that a supervisor must approve", async () => {
    mockApi({ "GET /api/auth/me": SUPERVISOR, "GET /api/hospital/queue": queue });
    const first = renderApp("/hospital");
    expect(await within(await screen.findByTestId("queue-second")).findByRole("link", { name: /Approve/ })).toHaveAttribute("href", "/hospital/cases/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    first.unmount();
    mockApi({ "GET /api/auth/me": CLERK, "GET /api/hospital/queue": queue });
    renderApp("/hospital");
    expect(await within(await screen.findByTestId("queue-second")).findByText("A billing supervisor must approve")).toBeInTheDocument();
  });

  it("does not offer the approval to the person who confirmed the bill", async () => {
    const own = { ...queue, awaiting_second_approval: [{ ...queue.awaiting_second_approval[0], confirmed_by: SUPERVISOR.display_name }] };
    mockApi({ "GET /api/auth/me": SUPERVISOR, "GET /api/hospital/queue": own });
    renderApp("/hospital");
    expect(await within(await screen.findByTestId("queue-second")).findByText("A billing supervisor must approve")).toBeInTheDocument();
  });

  it("says when nothing is waiting", async () => {
    mockApi({ "GET /api/auth/me": CLERK, "GET /api/hospital/queue": { pending: [], awaiting_second_approval: [] } });
    renderApp("/hospital");
    expect(await screen.findByText("Nothing is waiting")).toBeInTheDocument();
    expect(screen.getByText("No bills are waiting for a second approval.")).toBeInTheDocument();
  });
});

describe("bill review", () => {
  const base = { "GET /api/auth/me": CLERK, [`GET ${HPATH}`]: hospitalCase() };

  it("shows the patient number, the original bill and what the software read beside it", async () => {
    mockApi(base);
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Zawadi" })).toBeInTheDocument();
    expect(screen.getByText("KMH-ADM-7731")).toBeInTheDocument();
    expect(screen.getByTitle("The uploaded hospital bill (PDF)")).toHaveAttribute("src", "https://files.example.test/bill.pdf?signature=abc");
    expect(screen.getAllByText("KES 160,430", { selector: "dd" })).toHaveLength(2); // the total and the balance due
    expect(screen.getByText("General ward bed (per day)")).toBeInTheDocument();
    expect(screen.getByText("The amounts add up.")).toBeInTheDocument();
    expect(screen.getByText("Low concern")).toBeInTheDocument();
  });

  it("says plainly when the original has been deleted", async () => {
    mockApi({ ...base, [`GET ${HPATH}`]: hospitalCase({ bills: [{ id: "b", kind: "invoice", mime_type: "application/pdf", page_count: 1, created_at: "2026-10-23T08:20:00+00:00", preview_url: null }] }) });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText(/The original file has been deleted/)).toBeInTheDocument();
  });

  it("does not fill the amount in, and says whether what was typed matches what was read", async () => {
    mockApi(base);
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    const amount = await screen.findByLabelText("Total amount to be paid (KES)");
    expect(amount).toHaveValue("");
    expect(screen.getByLabelText("Invoice number")).toHaveValue("");
    await userEvent.type(amount, "160430");
    expect(await screen.findByText("This matches the amount our software read.")).toBeInTheDocument();
    await userEvent.clear(amount);
    await userEvent.type(amount, "150000");
    expect(await screen.findByText(/different from the KES 160,430 our software read/)).toBeInTheDocument();
  });

  it("will not send a decision before a code has been sent, and checks the form first", async () => {
    const api = mockApi(base);
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText("Enter the total from the bill.")).toBeInTheDocument();
    expect(screen.getByText("Enter the invoice number from the bill.")).toBeInTheDocument();
    expect(screen.getByText("Send a code and enter it before you submit.")).toBeInTheDocument();
    expect(api.called("POST", `${HPATH}/decision`)).toHaveLength(0);
  });

  it("sends the code to the register address only, then confirms with the code, amount and invoice number", async () => {
    const api = mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: decided("confirmed", "live") });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Send a code to bil\*\*\*@kilele-hospital.test/ }));
    expect(await screen.findByText(/A code was sent to bil\*\*\*@kilele-hospital.test/)).toBeInTheDocument();
    expect(api.called("POST", `${HPATH}/otp`)[0]?.body).toEqual({ purpose: "decision" });
    await userEvent.type(screen.getByLabelText("Total amount to be paid (KES)"), "160430");
    await userEvent.type(screen.getByLabelText("Invoice number"), "KMH/26/024405");
    await typeCode("482913");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText(/The bill is confirmed. The family's page is now live/)).toBeInTheDocument();
    expect(api.called("POST", `${HPATH}/decision`)[0]?.body).toEqual({
      request_id: "88888888-8888-4888-8888-888888888888",
      otp: "482913",
      decision: "confirmed",
      amount_kes: "160430.00",
      invoice_number: "KMH/26/024405",
      is_final_bill: false,
      deposit_kes: null,
      note: null,
    });
    expect(api.called("POST", `${HPATH}/decision`)[0]?.csrf).toBe("test-token");
  });

  it("includes the deposit and the final bill choice when given", async () => {
    const api = mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: decided("confirmed", "live") });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Total amount to be paid (KES)"), "100000");
    await userEvent.type(screen.getByLabelText("Invoice number"), "INV-1");
    await userEvent.type(screen.getByLabelText("Deposit already paid (optional)"), "20000.50");
    await userEvent.click(screen.getByRole("checkbox", { name: /This is the final bill/ }));
    await typeCode("111111");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    await screen.findByText(/Decision recorded/);
    expect(api.called("POST", `${HPATH}/decision`)[0]?.body).toMatchObject({ amount_kes: "100000.00", deposit_kes: "20000.50", is_final_bill: true });
  });

  it("warns about the second approval for a large amount, and reports that it is needed", async () => {
    mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: decided("awaiting_second_approval", "pending_confirmation") });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    const amount = await screen.findByLabelText("Total amount to be paid (KES)");
    await userEvent.type(amount, "199999");
    expect(screen.queryByText(/need a second approval/)).not.toBeInTheDocument();
    await userEvent.clear(amount);
    await userEvent.type(amount, "250000");
    expect(await screen.findByText(/Bills of KES 200,000 or more need a second approval/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Invoice number"), "INV-2");
    await typeCode("222222");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText(/needs a second approval from a different billing supervisor/)).toBeInTheDocument();
  });

  it("shows a wrong code next to the code and lets the clerk try again", async () => {
    let tries = 0;
    mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: () => (tries++ === 0 ? { status: 422, body: { error: { code: "otp_invalid", message: "x" } } } : decided("confirmed", "live").body) });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Total amount to be paid (KES)"), "160430");
    await userEvent.type(screen.getByLabelText("Invoice number"), "INV-3");
    await typeCode("000000");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText("That code is not right. Check it and try again.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("textbox", { name: /Digit 1 of 6/ }));
    for (let i = 0; i < 6; i++) await userEvent.keyboard("{Backspace}");
    await typeCode("123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText(/The bill is confirmed/)).toBeInTheDocument();
  });

  it("asks for a new code when the old one expired or locked", async () => {
    mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: { status: 422, body: { error: { code: "otp_locked", message: "x" } } } });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Total amount to be paid (KES)"), "160430");
    await userEvent.type(screen.getByLabelText("Invoice number"), "INV-4");
    await typeCode("000000");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText("Too many wrong tries. Send a new code.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Send a code to/ })).toBeInTheDocument();
  });

  it("shows the server's refusal of a used invoice number beside that field", async () => {
    mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: { status: 409, body: { error: { code: "invoice_already_used", message: "x" } } } });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Total amount to be paid (KES)"), "160430");
    await userEvent.type(screen.getByLabelText("Invoice number"), "KMH/DUP");
    await typeCode("123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm the bill" }));
    expect(await screen.findByText("That invoice number is already used on another case at this hospital.")).toBeInTheDocument();
  });

  it("asks the family a question with a note instead of an amount", async () => {
    const api = mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: decided("queried", "pending_confirmation") });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("radio", { name: /Ask the family a question/ }));
    expect(screen.queryByLabelText("Total amount to be paid (KES)")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.click(screen.getByRole("button", { name: "Send the question" }));
    expect(await screen.findByText("Write at least a few words for the family.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question for the family"), "The total does not match the lines.");
    await typeCode("123456");
    await userEvent.click(screen.getByRole("button", { name: "Send the question" }));
    expect(await screen.findByText(/Your question has gone to the family/)).toBeInTheDocument();
    expect(api.called("POST", `${HPATH}/decision`)[0]?.body).toMatchObject({ decision: "queried", note: "The total does not match the lines.", amount_kes: null, invoice_number: null });
  });

  it("rejects a bill that is not the hospital's", async () => {
    const api = mockApi({ ...base, [`POST ${HPATH}/otp`]: otpSent, [`POST ${HPATH}/decision`]: decided("rejected", "in_review") });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    await userEvent.click(await screen.findByRole("radio", { name: /This is not our bill/ }));
    await userEvent.click(screen.getByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    await userEvent.type(screen.getByLabelText("Why is this not your bill?"), "We never issued this invoice.");
    await typeCode("123456");
    await userEvent.click(screen.getByRole("button", { name: "Reject the bill" }));
    expect(await screen.findByText(/The bill was not confirmed/)).toBeInTheDocument();
    expect(api.called("POST", `${HPATH}/decision`)[0]?.body).toMatchObject({ decision: "rejected" });
  });

  it("on a final bill for a live case needs no final-bill tick and hides the deposit", async () => {
    mockApi({ ...base, [`GET ${HPATH}`]: hospitalCase({ status: "live", bills: [{ id: "f", kind: "final_bill", mime_type: "application/pdf", page_count: 1, created_at: "2026-10-23T08:20:00+00:00", preview_url: null }] }) });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText(/final bill for a case that is already live/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Deposit already paid (optional)")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /final bill/ })).not.toBeInTheDocument();
  });

  it("shows earlier decisions with who made them and the PayPal invoice status", async () => {
    mockApi({ ...base, [`GET ${HPATH}`]: hospitalCase({ status: "live", confirmations: [confirmationRecord({ status: "confirmed", second_approver: "Kilele Mission Hospital supervisor", invoice_status: "SENT" })] }) });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText("Earlier decisions")).toBeInTheDocument();
    expect(screen.getByText("PayPal invoice: SENT")).toBeInTheDocument();
    expect(screen.getByText(/second approval by Kilele Mission Hospital supervisor/)).toBeInTheDocument();
    expect(screen.getByText("This bill is not waiting for a decision.")).toBeInTheDocument();
  });

  it("says when a case belongs to another hospital", async () => {
    mockApi({ "GET /api/auth/me": CLERK, [`GET ${HPATH}`]: { status: 404, body: { error: { code: "case_not_found", message: "x" } } } });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText("We could not find that bill")).toBeInTheDocument();
  });
});

describe("second approval", () => {
  const waiting = hospitalCase({ confirmations: [confirmationRecord()] });

  it("lets a different supervisor approve with a fresh code", async () => {
    const api = mockApi({
      "GET /api/auth/me": SUPERVISOR,
      [`GET ${HPATH}`]: waiting,
      [`POST ${HPATH}/otp`]: otpSent,
      "POST /api/hospital/confirmations/66666666-6666-4666-8666-666666666666/approve": decided("confirmed", "live"),
    });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText("Second approval needed")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your decision" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Send a code to/ }));
    await screen.findByText(/A code was sent/);
    expect(api.called("POST", `${HPATH}/otp`)[0]?.body).toEqual({ purpose: "second_approval" });
    await typeCode("654321");
    await userEvent.click(screen.getByRole("button", { name: "Approve the bill" }));
    expect(await screen.findByText(/Both approvals are in/)).toBeInTheDocument();
    expect(api.called("POST", "/api/hospital/confirmations/66666666-6666-4666-8666-666666666666/approve")[0]?.body).toEqual({ request_id: "88888888-8888-4888-8888-888888888888", otp: "654321" });
  });

  it("does not let the person who confirmed give the second approval", async () => {
    mockApi({ "GET /api/auth/me": { ...SUPERVISOR, display_name: "Kilele Mission Hospital clerk" }, [`GET ${HPATH}`]: waiting });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText(/A different billing supervisor must give the second approval/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send a code to/ })).not.toBeInTheDocument();
  });

  it("tells a clerk that only a supervisor can approve", async () => {
    mockApi({ "GET /api/auth/me": CLERK, [`GET ${HPATH}`]: hospitalCase({ confirmations: [confirmationRecord({ confirmed_by: "Someone else" })] }) });
    renderApp(`/hospital/cases/${HOSPITAL_CASE_ID}`);
    expect(await screen.findByText("Only a billing supervisor can give the second approval.")).toBeInTheDocument();
  });
});

describe("payments received", () => {
  const item = (overrides = {}) => ({ id: "s1", case_alias: "Baraka", tranche_no: 1, status: "succeeded", amount_kes_minor: 6_744_507, amount_kes: "67445.07", external_reference: "PAYOUT-0007", requested_at: "2026-10-18T14:13:00+00:00", completed_at: "2026-10-18T14:14:00+00:00", ...overrides });

  it("lists the payments the hospital received, with a note that they are simulated", async () => {
    mockApi({ "GET /api/auth/me": CLERK, "GET /api/hospital/settlements": { items: [item(), item({ id: "s2", tranche_no: 2, status: "processing", amount_kes_minor: 100000, completed_at: null })], page: 1, page_size: 50, total: 2 } });
    renderApp("/hospital/settlements");
    expect(await screen.findByRole("heading", { name: "Payments received" })).toBeInTheDocument();
    const grid = await screen.findByTestId("settlements-grid");
    expect(await within(grid).findByText("KES 67,445.07")).toBeInTheDocument();
    expect(within(grid).getByText("Paid")).toBeInTheDocument();
    expect(within(grid).getByText("Being paid")).toBeInTheDocument();
    expect(screen.getAllByText("Sandbox / simulated").length).toBeGreaterThan(0);
  });

  it("says when nothing has been paid yet", async () => {
    mockApi({ "GET /api/auth/me": CLERK, "GET /api/hospital/settlements": { items: [], page: 1, page_size: 50, total: 0 } });
    renderApp("/hospital/settlements");
    expect(await screen.findByText("No payments yet")).toBeInTheDocument();
    void waitFor;
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ADMIN, ADMIN_CASE_ID, ADMIN_STATS, adminCase, adminListItem, agentAction, APATH, auditEntry, JOBS, reviewItem } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const page = (items: unknown[], total = items.length) => ({ items, page: 1, page_size: 50, total });
const base = { "GET /api/auth/me": ADMIN };
const agentOn = { "GET /api/public/config": { paypal_client_id: "", paypal_mode: "mock", currency: "USD", demo_banner: false, features: { agent: true, appeal_checker: true } } };

describe("admin overview", () => {
  const overview = { ...base, "GET /api/admin/stats": ADMIN_STATS, "GET /api/admin/jobs": JOBS, "GET /api/admin/cases": page([adminListItem()]) };

  it("shows what is waiting for a person, the money, and cases by status", async () => {
    mockApi(overview);
    renderApp("/admin");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText("Cases to review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Cases to review" })).toHaveAttribute("href", "/admin/review");
    expect(screen.getByRole("link", { name: "Failed background jobs" })).toHaveAttribute("href", "/admin/jobs");
    expect(screen.getByText("$5,288.18")).toBeInTheDocument();
    expect(screen.getByText("KES 649,327.95")).toBeInTheDocument();
    expect(screen.getByText("6 cases")).toBeInTheDocument();
    expect(screen.getByText("Live: 3")).toBeInTheDocument();
    expect(await screen.findByText(/Jobs waiting in the queues — triage: 1/)).toBeInTheDocument();
  });

  it("warns when the job queues cannot be reached", async () => {
    mockApi({ ...overview, "GET /api/admin/stats": { ...ADMIN_STATS, queues: null } });
    renderApp("/admin");
    expect(await screen.findByText("The job queues cannot be reached.")).toBeInTheDocument();
  });

  it("checks the whole ledger on request and reports an intact chain or the first bad entry", async () => {
    let valid = true;
    mockApi({ ...overview, "GET /api/admin/ledger/verify": () => ({ valid, entries_checked: 73, last_hash: "ab", first_bad_seq: valid ? null : 41, checked_at: "x" }) });
    renderApp("/admin");
    await userEvent.click(await screen.findByRole("button", { name: "Check the whole ledger" }));
    expect(await screen.findByText("The ledger is intact")).toBeInTheDocument();
    valid = false;
    await userEvent.click(screen.getByRole("button", { name: "Check the whole ledger" }));
    expect(await screen.findByText("The ledger is broken")).toBeInTheDocument();
    expect(screen.getByText(/fails at entry 41/)).toBeInTheDocument();
  });

  it("lists cases with a visible Open link, and searches", async () => {
    const api = mockApi(overview);
    renderApp("/admin");
    const grid = await screen.findByTestId("data-grid");
    expect(await within(grid).findByText("family2@example.test")).toBeInTheDocument();
    expect(within(grid).getByRole("link", { name: /Open\s*:\s*Zawadi/ })).toHaveAttribute("href", `/admin/cases/${ADMIN_CASE_ID}`);
    await userEvent.type(screen.getByLabelText(/Search by alias/), "kilele");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(api.calls.filter((call) => call.path === "/api/admin/cases").length).toBeGreaterThan(1));
  });
});

describe("review queue", () => {
  it("lists open items first and resolves one with a decision and a required reason", async () => {
    let items = [reviewItem()];
    const api = mockApi({
      ...base,
      "GET /api/admin/review-items": () => page(items),
      "POST /api/admin/review-items/cccccccc-cccc-4ccc-8ccc-cccccccccccc/resolve": () => {
        items = [reviewItem({ status: "resolved", resolution: "approved_for_confirmation", resolution_note: "Fine." })];
        return items[0];
      },
    });
    renderApp("/admin/review");
    expect(await screen.findByRole("heading", { level: 1, name: "Review queue" })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /Resolve\s*:\s*Zawadi/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("The bill looks like another one we have seen")).toBeInTheDocument();
    expect(within(dialog).getByText("duplicate_fingerprint", { exact: false })).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Record decision" });
    expect(submit).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("radio", { name: /Keep unverified/ }));
    await userEvent.type(within(dialog).getByLabelText("Why? (required)"), "Looks like a re-save of the same bill.");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(api.called("POST", "/api/admin/review-items/cccccccc-cccc-4ccc-8ccc-cccccccccccc/resolve")[0]?.body).toEqual({ resolution: "kept_unverified", note: "Looks like a re-save of the same bill." });
  });

  it("explains when the item was already resolved", async () => {
    mockApi({
      ...base,
      "GET /api/admin/review-items": page([reviewItem()]),
      "POST /api/admin/review-items/cccccccc-cccc-4ccc-8ccc-cccccccccccc/resolve": { status: 409, body: { error: { code: "conflict", message: "x" } } },
    });
    renderApp("/admin/review");
    await userEvent.click(await screen.findByRole("button", { name: /Resolve\s*:\s*Zawadi/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Why? (required)"), "A reason that is long enough.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    expect(await within(dialog).findByText(/already resolved, or the case cannot take that decision/)).toBeInTheDocument();
  });

  it("shows a resolved item's decision without a form", async () => {
    mockApi({ ...base, "GET /api/admin/review-items": page([reviewItem({ status: "resolved", resolution: "no_action", resolution_note: "All good." })]) });
    renderApp("/admin/review");
    await userEvent.click(await screen.findByRole("button", { name: /View\s*:\s*Zawadi/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("No action needed")).toBeInTheDocument();
    expect(within(dialog).getByText("All good.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Record decision" })).not.toBeInTheDocument();
  });

  it("says when the queue is empty", async () => {
    mockApi({ ...base, "GET /api/admin/review-items": page([]) });
    renderApp("/admin/review");
    expect(await screen.findByText("Nothing here")).toBeInTheDocument();
  });
});

describe("admin case detail", () => {
  it("shows everything about the case, and says that opening it is audited", async () => {
    mockApi({ ...base, [`GET ${APATH}`]: adminCase() });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Zawadi" })).toBeInTheDocument();
    expect(screen.getByText("Demo Family 2 · family2@example.test")).toBeInTheDocument();
    expect(screen.getByText("KMH-ADM-7731")).toBeInTheDocument();
    expect(screen.getByText(/Opening this page is written to the audit log/)).toBeInTheDocument();
    expect(screen.getByText("Risk score 12")).toBeInTheDocument();
    expect(screen.getByText("local checks only")).toBeInTheDocument();
    expect(screen.getByText("Amina")).toBeInTheDocument();
    expect(screen.getByText(/failed: payout_declined/)).toBeInTheDocument();
    expect(screen.getByText("admin.case_viewed")).toBeInTheDocument();
  });

  it("freezes a case only after a reason and an explicit confirmation", async () => {
    const api = mockApi({ ...base, [`GET ${APATH}`]: adminCase(), [`POST ${APATH}/freeze`]: adminCase({ status: "frozen", freeze_reason: "Testing", frozen_at: "2026-10-23T10:00:00+00:00" }) });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Freeze case" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/every donor is refunded in full, whatever they chose/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Freeze and refund" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Why? (required)"), "Suspected fraud, see review item");
    await userEvent.click(confirm);
    expect(await screen.findByText(/Paused because: Testing/)).toBeInTheDocument();
    expect(api.called("POST", `${APATH}/freeze`)[0]?.body).toEqual({ reason: "Suspected fraud, see review item" });
    expect(screen.queryByRole("button", { name: "Freeze case" })).not.toBeInTheDocument();
  });

  it("keeps the freeze dialog open and explains when the case cannot be frozen", async () => {
    mockApi({ ...base, [`GET ${APATH}`]: adminCase(), [`POST ${APATH}/freeze`]: { status: 409, body: { error: { code: "invalid_transition", message: "x" } } } });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Freeze case" }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.type(within(dialog).getByLabelText("Why? (required)"), "Suspected fraud, see review item");
    await userEvent.click(within(dialog).getByRole("button", { name: "Freeze and refund" }));
    expect(await within(dialog).findByText("This case cannot be frozen from where it is.")).toBeInTheDocument();
  });

  it("offers no freeze for a case that has ended", async () => {
    mockApi({ ...base, [`GET ${APATH}`]: adminCase({ status: "closed" }) });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    await screen.findByRole("heading", { level: 1, name: "Zawadi" });
    expect(screen.queryByRole("button", { name: "Freeze case" })).not.toBeInTheDocument();
  });

  it("checks for a payment now and says why none was due", async () => {
    mockApi({ ...base, [`GET ${APATH}`]: adminCase(), [`POST ${APATH}/settlements/run`]: { reason: "below_threshold", settlement_id: null, tranche_no: null, amount_usd_minor: null, amount_kes_minor: null, status: null } });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    await userEvent.click(await screen.findByRole("button", { name: "Check for a payment now" }));
    expect(await screen.findByText("Not enough has been pledged yet for the next payment.")).toBeInTheDocument();
  });

  it("says when a case does not exist", async () => {
    mockApi({ ...base, [`GET ${APATH}`]: { status: 404, body: { error: { code: "case_not_found", message: "x" } } } });
    renderApp(`/admin/cases/${ADMIN_CASE_ID}`);
    expect(await screen.findByText("We could not find that case")).toBeInTheDocument();
  });
});

describe("audit log", () => {
  it("lists entries, filters by target, and shows an entry's details", async () => {
    const api = mockApi({ ...base, "GET /api/admin/audit-log": page([auditEntry(), auditEntry({ id: "ffffffff-ffff-4fff-8fff-ffffffffffff", action: "auth.login" })]) });
    renderApp("/admin/audit");
    const grid = await screen.findByTestId("data-grid");
    expect(await within(grid).findByText("auth.login")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Target type"), "case");
    await userEvent.type(screen.getByLabelText("Target ID (the full ID)"), ADMIN_CASE_ID);
    await userEvent.click(screen.getByRole("button", { name: "Filter" }));
    await waitFor(() => expect(api.calls.some((call) => call.path === "/api/admin/audit-log" && call.method === "GET")).toBe(true));
    await userEvent.click((await screen.findAllByRole("button", { name: /Details\s*:\s*admin.case_viewed/ }))[0]!);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/"note": "opened"/)).toBeInTheDocument();
    expect(within(dialog).getByText("req-1")).toBeInTheDocument();
  });

  it("rejects a half-typed ID before asking the server", async () => {
    const api = mockApi({ ...base, "GET /api/admin/audit-log": page([auditEntry()]) });
    renderApp("/admin/audit");
    await screen.findByTestId("data-grid");
    const before = api.calls.filter((call) => call.path === "/api/admin/audit-log").length;
    await userEvent.type(screen.getByLabelText("Target ID (the full ID)"), "abc");
    expect(await screen.findByText("That is not a full ID.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Filter" }));
    expect(api.calls.filter((call) => call.path === "/api/admin/audit-log")).toHaveLength(before);
  });
});

describe("background jobs", () => {
  it("shows the queues and the scheduler, and puts a failed job back on its queue", async () => {
    const api = mockApi({ ...base, "GET /api/admin/jobs": JOBS, "POST /api/admin/jobs/job-1/retry": { id: "job-1", queue: "payments", status: "queued" } });
    renderApp("/admin/jobs");
    expect(await screen.findByText(/The scheduler is running/)).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "payments" })).toBeInTheDocument();
    const job = await screen.findByTestId("failed-job");
    expect(within(job).getByText("app.jobs.tasks.run_settlement_check", { selector: "p" })).toBeInTheDocument();
    expect(within(job).getByText("PayPalError: 502 unavailable")).toBeInTheDocument();
    await userEvent.click(within(job).getByRole("button", { name: /Retry/ }));
    await waitFor(() => expect(api.called("POST", "/api/admin/jobs/job-1/retry")).toHaveLength(1));
    expect(api.called("POST", "/api/admin/jobs/job-1/retry")[0]?.csrf).toBe("test-token");
  });

  it("says when the scheduler has stopped and when nothing failed", async () => {
    mockApi({ ...base, "GET /api/admin/jobs": { ...JOBS, failed_jobs: [], scheduler: { last_heartbeat: null, age_seconds: null, healthy: false } } });
    renderApp("/admin/jobs");
    expect(await screen.findByText(/The scheduler has stopped/)).toBeInTheDocument();
    expect(screen.getByText("No failed jobs")).toBeInTheDocument();
  });
});

describe("treasurer agent", () => {
  it("is explained as switched off when the feature is off, and asks nothing of the agent routes", async () => {
    const api = mockApi({ ...base });
    renderApp("/admin/agent");
    expect(await screen.findByText("The treasurer agent is not switched on")).toBeInTheDocument();
    expect(api.calls.filter((call) => call.path.startsWith("/api/admin/agent"))).toHaveLength(0);
  });

  it("answers with the trace of what it looked at and its proposals, which wait for a person", async () => {
    const api = mockApi({
      ...base,
      ...agentOn,
      "GET /api/admin/cases": page([adminListItem()]),
      "GET /api/admin/agent/actions": page([]),
      "POST /api/admin/agent/ask": { answer: "One donation is unrefunded.", tool_calls: [{ name: "list_donations", input: { case_id: "x" }, ok: true, summary: "3 rows" }], proposed_actions: [agentAction()], steps: 2, stopped_early: false },
    });
    renderApp("/admin/agent");
    await userEvent.type(await screen.findByLabelText("Your question"), "Which donations are unrefunded?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("One donation is unrefunded.")).toBeInTheDocument();
    expect(screen.getByText("What it looked at (1 step)")).toBeInTheDocument();
    expect(screen.getByText("list_donations")).toBeInTheDocument();
    const action = (await screen.findAllByTestId("agent-action"))[0]!;
    expect(within(action).getByText("Waiting for you")).toBeInTheDocument();
    expect(api.called("POST", "/api/admin/agent/ask")[0]?.body).toEqual({ question: "Which donations are unrefunded?", case_id: null });
  });

  it("explains when no language model is set up", async () => {
    mockApi({ ...base, ...agentOn, "GET /api/admin/cases": page([]), "GET /api/admin/agent/actions": page([]), "POST /api/admin/agent/ask": { status: 503, body: { error: { code: "llm_not_configured", message: "x" } } } });
    renderApp("/admin/agent");
    await userEvent.type(await screen.findByLabelText("Your question"), "Anything unusual?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/No language model is set up in this environment/)).toBeInTheDocument();
  });

  it("approves a proposal only after an explicit confirmation", async () => {
    const api = mockApi({
      ...base,
      ...agentOn,
      "GET /api/admin/cases": page([]),
      "GET /api/admin/agent/actions": page([agentAction()]),
      "POST /api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/approve": agentAction({ status: "executed", decided_by: "Demo Admin" }),
    });
    renderApp("/admin/agent");
    await userEvent.click(await screen.findByRole("button", { name: /^Approve/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("The donor is refunded through PayPal straight away.")).toBeInTheDocument();
    expect(api.called("POST", "/api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/approve")).toHaveLength(0);
    await userEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(api.called("POST", "/api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/approve")).toHaveLength(1));
  });

  it("needs a reason to reject a proposal", async () => {
    const api = mockApi({
      ...base,
      ...agentOn,
      "GET /api/admin/cases": page([]),
      "GET /api/admin/agent/actions": page([agentAction()]),
      "POST /api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/reject": agentAction({ status: "rejected" }),
    });
    renderApp("/admin/agent");
    await userEvent.click(await screen.findByRole("button", { name: /^Reject/ }));
    const dialog = await screen.findByRole("dialog");
    const send = within(dialog).getByRole("button", { name: "Reject" });
    expect(send).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Why? (required)"), "The donor already got a refund by email.");
    await userEvent.click(send);
    await waitFor(() => expect(api.called("POST", "/api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/reject")).toHaveLength(1));
    expect(api.called("POST", "/api/admin/agent/actions/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/reject")[0]?.body).toEqual({ note: "The donor already got a refund by email." });
  });

  it("shows why a proposal could not be carried out", async () => {
    mockApi({ ...base, ...agentOn, "GET /api/admin/cases": page([]), "GET /api/admin/agent/actions": page([agentAction({ status: "failed", error: "The donation was already refunded." })]) });
    renderApp("/admin/agent");
    await userEvent.click(await screen.findByRole("tab", { name: "All" }));
    expect(await screen.findByText("The donation was already refunded.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve/ })).not.toBeInTheDocument();
  });
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { violationsIn } from "@/test/axe";
import { ADMIN, ADMIN_CASE_ID, ADMIN_STATS, adminCase, adminListItem, APATH, CASE_BASE, CASE_ID, CLERK, confirmationRecord, consentRecord, documentRecord, FAMILY_BASE, familyCase, HOSPITAL_CASE_ID, hospitalCase, HOSPITALS, HPATH, JOBS, publicCase, RECEIPT, reviewItem, STATS } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const page = (items: unknown[]) => ({ items, page: 1, page_size: 50, total: items.length });

async function expectAccessible(path: string, ready: () => Promise<unknown>) {
  const view = renderApp(path);
  await ready();
  const found = await violationsIn(view.container);
  view.unmount(); // one page per document, or the shells would count as duplicate landmarks
  expect(found).toEqual([]);
}

describe("accessibility of the main pages (structure, names and roles)", () => {
  it("public pages", async () => {
    mockApi({ ...CASE_BASE, "GET /api/public/stats": STATS, "GET /api/hospitals": HOSPITALS, "GET /api/public/cases": page([publicCase()]), "GET /api/public/cases/demolive2345": publicCase(), "GET /api/donations/receipts/tok123": RECEIPT });
    await expectAccessible("/", () => screen.findByRole("link", { name: "Baraka" }));
    await expectAccessible("/cases", () => screen.findAllByTestId("case-card"));
    await expectAccessible("/how-it-works", () => screen.findByRole("heading", { level: 1 }));
    await expectAccessible("/c/demolive2345", () => screen.findByTestId("ledger-grid"));
    await expectAccessible("/receipt/tok123", () => screen.findByRole("heading", { name: "Thank you" }));
    await expectAccessible("/privacy", () => screen.findByRole("heading", { level: 1, name: "Privacy" }));
    await expectAccessible("/check", () => screen.findByRole("heading", { level: 1 }));
    await expectAccessible("/login", () => screen.findByRole("heading", { level: 1, name: "Sign in" }));
    await expectAccessible("/nowhere", () => screen.findByRole("heading", { level: 1, name: "Page not found" }));
  });

  it("family pages", async () => {
    const path = `/api/cases/${CASE_ID}`;
    mockApi({
      ...FAMILY_BASE,
      "GET /api/cases": page([familyCase()]),
      [`GET ${path}`]: familyCase({ status: "pending_confirmation", consents: ["hospital_verification", "public_page"], triage: { risk_band: "low", route: "hospital_confirmation", reasons_en: ["The amounts add up."], reasons_sw: [], created_at: "2026-10-23T08:05:00+00:00" } }),
      [`GET ${path}/consents`]: { items: [consentRecord("hospital_verification")] },
      [`GET ${path}/documents`]: { items: [documentRecord()] },
    });
    await expectAccessible("/app", () => screen.findByTestId("family-case"));
    await expectAccessible(`/app/cases/${CASE_ID}`, () => screen.findByRole("heading", { level: 1, name: "Zawadi" }));
  });

  it("every step of the wizard", async () => {
    const path = `/api/cases/${CASE_ID}`;
    mockApi({
      ...FAMILY_BASE,
      [`GET ${path}`]: familyCase(),
      [`GET ${path}/consents`]: { items: [consentRecord("hospital_verification"), consentRecord("public_page")] },
      [`GET ${path}/documents`]: { items: [documentRecord()] },
    });
    await expectAccessible("/app/new", () => screen.findByLabelText("Search hospitals"));
    await expectAccessible(`/app/new?case=${CASE_ID}&step=1`, () => screen.findByLabelText("Name for your page"));
    await expectAccessible(`/app/new?case=${CASE_ID}&step=2`, () => screen.findByRole("heading", { name: "What you agree to" }));
    await expectAccessible(`/app/new?case=${CASE_ID}&step=3`, () => screen.findByRole("heading", { name: "Upload the hospital bill" }));
    await expectAccessible(`/app/new?case=${CASE_ID}&step=4`, () => screen.findByLabelText("Your story (optional)"));
    await expectAccessible(`/app/new?case=${CASE_ID}&step=5`, () => screen.findByRole("heading", { name: "Check and send" }));
  });

  it("hospital pages", async () => {
    mockApi({
      "GET /api/auth/me": CLERK,
      "GET /api/hospital/queue": { pending: [{ id: HOSPITAL_CASE_ID, alias: "Zawadi", region: "Nairobi", submitted_at: "2026-10-23T08:30:00+00:00", risk_band: "low", bills: 1, kind: "first_bill" }], awaiting_second_approval: [{ ...confirmationRecord(), case_id: HOSPITAL_CASE_ID, case_alias: "Neema" }] },
      [`GET ${HPATH}`]: hospitalCase({ confirmations: [confirmationRecord({ status: "confirmed" })] }),
      "GET /api/hospital/settlements": page([{ id: "s1", case_alias: "Baraka", tranche_no: 1, status: "succeeded", amount_kes_minor: 100000, amount_kes: "1000.00", external_reference: "P-1", requested_at: "2026-10-18T14:13:00+00:00", completed_at: null }]),
    });
    await expectAccessible("/hospital", () => screen.findByTestId("queue-pending"));
    await expectAccessible(`/hospital/cases/${HOSPITAL_CASE_ID}`, () => screen.findByRole("heading", { name: "Your decision" }));
    await expectAccessible("/hospital/settlements", () => screen.findByTestId("settlements-grid"));
  });

  it("admin pages", async () => {
    mockApi({
      "GET /api/auth/me": ADMIN,
      "GET /api/admin/stats": ADMIN_STATS,
      "GET /api/admin/jobs": JOBS,
      "GET /api/admin/cases": page([adminListItem()]),
      "GET /api/admin/review-items": page([reviewItem()]),
      "GET /api/admin/audit-log": page([]),
      [`GET ${APATH}`]: adminCase(),
    });
    await expectAccessible("/admin", () => screen.findByTestId("data-grid"));
    await expectAccessible("/admin/review", () => screen.findByTestId("data-grid"));
    await expectAccessible("/admin/jobs", () => screen.findByTestId("failed-job"));
    await expectAccessible(`/admin/cases/${ADMIN_CASE_ID}`, () => screen.findByRole("heading", { level: 1, name: "Zawadi" }));
  });

  it("dialogs when open", async () => {
    mockApi({ "GET /api/auth/me": ADMIN, "GET /api/admin/review-items": page([reviewItem()]) });
    const view = renderApp("/admin/review");
    await userEvent.click(await screen.findByRole("button", { name: /Resolve\s*:\s*Zawadi/ }));
    await screen.findByRole("dialog");
    expect(await violationsIn(document.body)).toEqual([]);
    view.unmount();
  });
});

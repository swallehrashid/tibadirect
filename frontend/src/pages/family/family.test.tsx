import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CASE_ID, consentRecord, documentRecord, FAMILY_BASE, familyCase, MONEY_ZERO } from "@/test/fixtures";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const page = (items: unknown[]) => ({ items, page: 1, page_size: 50, total: items.length });
/** The list view carries the short form of a case: no consents, timeline, checks or confirmations. */
const listItem = (overrides: Record<string, unknown> = {}) => {
  const full = familyCase(overrides) as Record<string, unknown>;
  for (const key of ["consents", "timeline", "triage", "confirmations"]) delete full[key];
  return full;
};

describe("family home", () => {
  it("invites a new family to start their first campaign", async () => {
    mockApi({ ...FAMILY_BASE, "GET /api/cases": page([]) });
    renderApp("/app");
    expect(await screen.findByText("You have not started a campaign yet")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Start a campaign" }).every((a) => a.getAttribute("href") === "/app/new")).toBe(true);
  });

  it("lists cases with the next thing to do, and sends a draft back into the wizard", async () => {
    mockApi({
      ...FAMILY_BASE,
      "GET /api/cases": page([
        listItem(),
        listItem({ id: "22222222-2222-4222-8222-222222222222", alias: "Baraka", status: "live", trust_level: "hospital_confirmed", money: { ...MONEY_ZERO, target_kes_minor: 24_000_000, pledged_kes_minor: 7_000_000, settled_kes_minor: 6_000_000 } }),
      ]),
    });
    renderApp("/app");
    const cards = await screen.findAllByTestId("family-case");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]!).getByText("Finish your campaign and send it for checking")).toBeInTheDocument();
    expect(within(cards[0]!).getByRole("link", { name: /Continue your campaign/ })).toHaveAttribute("href", `/app/new?case=${CASE_ID}&step=2`);
    expect(within(cards[1]!).getByText(/Money has been pledged and is being paid to the hospital/)).toBeInTheDocument();
    expect(within(cards[1]!).getByText("KES 70,000 pledged of KES 240,000")).toBeInTheDocument();
    expect(within(cards[1]!).getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/app/cases/22222222-2222-4222-8222-222222222222");
  });
});

describe("campaign wizard", () => {
  it("creates the draft from the first step and moves to consent", async () => {
    const api = mockApi({ ...FAMILY_BASE, "POST /api/cases": { status: 201, body: familyCase() }, [`GET /api/cases/${CASE_ID}`]: familyCase(), [`GET /api/cases/${CASE_ID}/consents`]: { items: [] } });
    const { router } = renderApp("/app/new");
    await userEvent.type(await screen.findByLabelText("Search hospitals"), "kilele");
    await userEvent.click(await screen.findByRole("radio", { name: /Kilele Mission Hospital/ }));
    expect(screen.queryByRole("radio", { name: /Mto Mwema/ })).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Name for your page"), "Zawadi");
    await userEvent.type(screen.getByLabelText("County or town"), "Nairobi");
    await userEvent.type(screen.getByLabelText("Patient or admission number from the bill"), "KMH-ADM-7731");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("heading", { name: "What you agree to" })).toBeInTheDocument();
    expect(api.called("POST", "/api/cases")[0]?.body).toEqual({ hospital_id: "e1aa1af1-95c6-4886-a50b-cbb0dc490a82", alias: "Zawadi", region: "Nairobi", patient_reference: "KMH-ADM-7731", is_minor: false });
    expect(router.state.location.search).toBe(`?case=${CASE_ID}&step=2`);
  });

  it("checks the first step before asking the server", async () => {
    const api = mockApi({ ...FAMILY_BASE });
    renderApp("/app/new");
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    expect(await screen.findByText("Choose a hospital from the list.")).toBeInTheDocument();
    expect(screen.getByText("Use between 2 and 40 characters.")).toBeInTheDocument();
    expect(screen.getByText("Enter the admission or patient number from the bill (3 to 60 characters).")).toBeInTheDocument();
    expect(api.called("POST", "/api/cases")).toHaveLength(0);
  });

  it("shows the server's refusal of an illness word in the page name beside that field", async () => {
    mockApi({ ...FAMILY_BASE, "POST /api/cases": { status: 422, body: { error: { code: "alias_medical_terms", message: "Do not describe the illness in the alias.", details: { json: { alias: ["Do not describe the illness in the alias."] } } } } } });
    renderApp("/app/new");
    await userEvent.click(await screen.findByRole("radio", { name: /Kilele Mission Hospital/ }));
    await userEvent.type(screen.getByLabelText("Name for your page"), "Cancer Kid");
    await userEvent.type(screen.getByLabelText("County or town"), "Nairobi");
    await userEvent.type(screen.getByLabelText("Patient or admission number from the bill"), "KMH-ADM-7731");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Please leave out illness words from the name.")).toBeInTheDocument();
  });

  it("asks for the two required consents, then records them with the wording version", async () => {
    const api = mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`GET /api/cases/${CASE_ID}/consents`]: { items: [] },
      [`POST /api/cases/${CASE_ID}/consents`]: (call: { body: unknown }) => ({ status: 201, body: consentRecord((call.body as { scope: string }).scope) }),
      [`GET /api/cases/${CASE_ID}/documents`]: { items: [] },
    });
    renderApp(`/app/new?case=${CASE_ID}&step=2`);
    await screen.findByRole("heading", { name: "What you agree to" });
    const next = screen.getByRole("button", { name: "Next" });
    expect(next).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /I agree: Let the hospital confirm the bill/ }));
    expect(next).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /I agree: Show this case on a public page/ }));
    expect(next).toBeEnabled();
    await userEvent.click(next);
    expect(await screen.findByRole("heading", { name: "Upload the hospital bill" })).toBeInTheDocument();
    const grants = api.called("POST", `/api/cases/${CASE_ID}/consents`).map((c) => c.body);
    expect(grants).toEqual([
      { scope: "hospital_verification", actor: "self", wording_version: "v1", language: "en" },
      { scope: "public_page", actor: "self", wording_version: "v1", language: "en" },
    ]);
  });

  it("needs a parent or guardian to confirm for a child, and records the consent as a guardian's", async () => {
    const api = mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase({ is_minor: true }),
      [`GET /api/cases/${CASE_ID}/consents`]: { items: [] },
      [`POST /api/cases/${CASE_ID}/consents`]: (call: { body: unknown }) => ({ status: 201, body: consentRecord((call.body as { scope: string }).scope, { actor: "guardian" }) }),
      [`GET /api/cases/${CASE_ID}/documents`]: { items: [] },
    });
    renderApp(`/app/new?case=${CASE_ID}&step=2`);
    await screen.findByRole("heading", { name: "What you agree to" });
    expect(screen.getByRole("checkbox", { name: /I agree: Let the hospital confirm the bill/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "I am this child's parent or legal guardian." }));
    await userEvent.click(screen.getByRole("checkbox", { name: /I agree: Let the hospital confirm the bill/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /I agree: Show this case on a public page/ }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByRole("heading", { name: "Upload the hospital bill" });
    expect(api.called("POST", `/api/cases/${CASE_ID}/consents`).every((c) => (c.body as { actor: string }).actor === "guardian")).toBe(true);
  });

  it("keeps a consent that was already given and cannot be unticked here", async () => {
    mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`GET /api/cases/${CASE_ID}/consents`]: { items: [consentRecord("hospital_verification"), consentRecord("public_page")] },
    });
    renderApp(`/app/new?case=${CASE_ID}&step=2`);
    await screen.findByRole("heading", { name: "What you agree to" });
    expect(screen.getByRole("checkbox", { name: /I agree: Let the hospital confirm the bill/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /I agree: Let the hospital confirm the bill/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("uploads a bill as a file with its kind, lists it, and will not go on without one", async () => {
    let docs: unknown[] = [];
    const api = mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`GET /api/cases/${CASE_ID}/documents`]: () => ({ items: docs }),
      [`POST /api/cases/${CASE_ID}/documents`]: () => {
        docs = [documentRecord()];
        return { status: 201, body: documentRecord() };
      },
    });
    const { container } = renderApp(`/app/new?case=${CASE_ID}&step=3`);
    await screen.findByRole("heading", { name: "Upload the hospital bill" });
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByText("Upload at least one bill to continue.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /An estimate/ }));
    const input = container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;
    await userEvent.upload(input, new File(["%PDF-1.7"], "bill.pdf", { type: "application/pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Upload this bill" }));
    expect(await screen.findByText("Received")).toBeInTheDocument();
    expect(api.called("POST", `/api/cases/${CASE_ID}/documents`)[0]?.body).toMatchObject({ kind: "estimate", file: { type: "application/pdf" } }); // jsdom reports the file name as "blob"; a browser sends the real one
    expect(api.called("POST", `/api/cases/${CASE_ID}/documents`)[0]?.csrf).toBe("test-token");
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("explains a refused upload in plain words", async () => {
    mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`GET /api/cases/${CASE_ID}/documents`]: { items: [] },
      [`POST /api/cases/${CASE_ID}/documents`]: { status: 415, body: { error: { code: "unsupported_media", message: "x" } } },
    });
    const { container } = renderApp(`/app/new?case=${CASE_ID}&step=3`);
    await screen.findByRole("heading", { name: "Upload the hospital bill" });
    await userEvent.upload(container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement, new File(["%PDF-1.7"], "bill.pdf", { type: "application/pdf" }));
    await userEvent.click(screen.getByRole("button", { name: "Upload this bill" }));
    expect(await screen.findByText("Please choose a PDF, PNG or JPEG file.")).toBeInTheDocument();
  });

  it("checks the story for medical words a moment after typing stops, names them, and blocks moving on", async () => {
    const api = mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`PATCH /api/cases/${CASE_ID}`]: (call: { body: unknown }) =>
        /cancer/.test(JSON.stringify(call.body))
          ? { status: 422, body: { error: { code: "story_medical_terms", message: "x", details: { json: { story: ["x"] }, terms: ["cancer"] } } } }
          : familyCase({ story: (call.body as { story: string }).story }),
    });
    renderApp(`/app/new?case=${CASE_ID}&step=4`);
    const box = await screen.findByLabelText("Your story (optional)");
    await userEvent.type(box, "My mother has cancer");
    expect(await screen.findByText(/Please leave out medical words/)).toBeInTheDocument();
    expect(screen.getByText("cancer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await userEvent.clear(box);
    await userEvent.type(box, "We need help with the bill");
    expect(await screen.findByText(/Saved. No medical words found/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
    expect(api.called("PATCH", `/api/cases/${CASE_ID}`).at(-1)?.body).toEqual({ story: "We need help with the bill" });
  });

  it("shows a summary and sends the case for checking", async () => {
    const api = mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase({ story: "We need help." }),
      [`GET /api/cases/${CASE_ID}/consents`]: { items: [consentRecord("hospital_verification"), consentRecord("public_page")] },
      [`GET /api/cases/${CASE_ID}/documents`]: { items: [documentRecord()] },
      [`POST /api/cases/${CASE_ID}/submit`]: { status: 202, body: familyCase({ status: "pending_triage" }) },
    });
    const { router } = renderApp(`/app/new?case=${CASE_ID}&step=5`);
    expect(await screen.findByRole("heading", { name: "Check and send" })).toBeInTheDocument();
    expect(screen.getByText("Kilele Mission Hospital")).toBeInTheDocument();
    expect(screen.getByText("1 bill uploaded")).toBeInTheDocument();
    expect(screen.getByText("We need help.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Send for checking" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/app/cases/${CASE_ID}`));
    expect(api.called("POST", `/api/cases/${CASE_ID}/submit`)).toHaveLength(1);
  });

  it("will not send until both required consents and a bill are there", async () => {
    mockApi({
      ...FAMILY_BASE,
      [`GET /api/cases/${CASE_ID}`]: familyCase(),
      [`GET /api/cases/${CASE_ID}/consents`]: { items: [consentRecord("public_page")] },
      [`GET /api/cases/${CASE_ID}/documents`]: { items: [] },
    });
    renderApp(`/app/new?case=${CASE_ID}&step=5`);
    expect(await screen.findByRole("button", { name: "Send for checking" })).toBeDisabled();
    expect(screen.getByText(/Both required consents and at least one bill are needed/)).toBeInTheDocument();
  });

  it("sends a case that has already been submitted to its dashboard instead of the wizard", async () => {
    mockApi({ ...FAMILY_BASE, [`GET /api/cases/${CASE_ID}`]: familyCase({ status: "pending_confirmation" }), [`GET /api/cases/${CASE_ID}/triage`]: { ready: false } });
    const { router } = renderApp(`/app/new?case=${CASE_ID}&step=2`);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/app/cases/${CASE_ID}`));
  });

  it("locks the hospital once the draft exists", async () => {
    mockApi({ ...FAMILY_BASE, [`GET /api/cases/${CASE_ID}`]: familyCase() });
    renderApp(`/app/new?case=${CASE_ID}&step=1`);
    expect(await screen.findByText(/The hospital cannot be changed once the draft is started/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Name for your page")).toHaveValue("Zawadi");
    expect(screen.getByRole("radio", { name: /Kilele Mission Hospital/ })).toBeChecked();
  });
});

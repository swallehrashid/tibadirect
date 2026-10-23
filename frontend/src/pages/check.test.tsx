import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { validateUpload } from "@/lib/uploads";
import { mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

afterEach(async () => {
  vi.unstubAllGlobals();
  await i18n.changeLanguage("en");
});

const anonymous = { "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "x" } } } };
const result = (overrides: Record<string, unknown> = {}) => ({
  score: 82,
  band: "high",
  flagged: true,
  reasons_en: ["It asks you to send money to a personal phone number.", "It pushes you to act at once."],
  reasons_sw: ["Inakuomba utume pesa kwa namba ya simu ya mtu binafsi."],
  matched_case: null,
  safety_tip_en: "Pay through the hospital-confirmed page, never to a personal number.",
  safety_tip_sw: "Lipa kupitia ukurasa uliothibitishwa na hospitali, kamwe si kwa namba ya simu ya mtu.",
  explanation: null,
  used_llm: false,
  ...overrides,
});
const message = "URGENT!! Please send money to 0712 000 000 today, my child is in hospital and we have no time.";

describe("appeal checker", () => {
  it("scores a pasted message and shows the reasons and the safety tip", async () => {
    const api = mockApi({ ...anonymous, "POST /api/public/checker/appeal": result() });
    renderApp("/check");
    const button = await screen.findByRole("button", { name: "Check this appeal" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText("The message, exactly as you received it"), message);
    expect(button).toBeEnabled();
    await userEvent.click(button);
    const card = await screen.findByTestId("checker-result");
    expect(within(card).getByText("This looks like a scam")).toBeInTheDocument();
    expect(within(card).getByRole("meter")).toHaveAttribute("aria-valuenow", "82");
    expect(within(card).getByText("It asks you to send money to a personal phone number.")).toBeInTheDocument();
    expect(within(card).getByText(/never to a personal number/)).toBeInTheDocument();
    expect(api.called("POST", "/api/public/checker/appeal")[0]?.body).toEqual({ text: message });
  });

  it("needs at least 20 characters before it will check", async () => {
    mockApi({ ...anonymous });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("The message, exactly as you received it"), "too short");
    expect(screen.getByText("Paste at least 20 characters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check this appeal" })).toBeDisabled();
  });

  it("links to the confirmed case when the appeal matches one", async () => {
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": result({ score: 5, band: "low", flagged: false, reasons_en: [], matched_case: { slug: "demolive2345", alias: "Baraka", hospital_name: "Kilele Mission Hospital", url: "http://x/c/demolive2345", note: "n" } }) });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("The message, exactly as you received it"), message);
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    expect(await screen.findByText("This matches a confirmed case")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the confirmed case" })).toHaveAttribute("href", "/c/demolive2345");
    expect(screen.getByText("Nothing worrying found")).toBeInTheDocument();
  });

  it("treats an empty match from the API as no match", async () => {
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": result({ matched_case: {} }) });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("The message, exactly as you received it"), message);
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    await screen.findByTestId("checker-result");
    expect(screen.queryByText("This matches a confirmed case")).not.toBeInTheDocument();
  });

  it("shows the reasons and the tip in Swahili when the language is Swahili", async () => {
    await i18n.changeLanguage("sw");
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": result() });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("Ujumbe, kama ulivyoupokea"), message);
    await userEvent.click(screen.getByRole("button", { name: "Kagua ombi hili" }));
    expect(await screen.findByText("Inakuomba utume pesa kwa namba ya simu ya mtu binafsi.")).toBeInTheDocument();
    expect(screen.getByText(/Lipa kupitia ukurasa uliothibitishwa/)).toBeInTheDocument();
  });

  it("sends a screenshot as a picture and accepts only PNG or JPEG", async () => {
    const api = mockApi({ ...anonymous, "POST /api/public/checker/appeal": result() });
    const { container } = renderApp("/check");
    await userEvent.click(await screen.findByRole("tab", { name: "Upload a screenshot" }));
    const input = container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement;
    await userEvent.upload(input, new File(["%PDF-1.7"], "scan.pdf", { type: "application/pdf" }), { applyAccept: false });
    expect(await screen.findByRole("alert")).toHaveTextContent("Please choose a PNG or JPEG picture.");
    await userEvent.upload(input, new File(["png"], "chat.png", { type: "image/png" }));
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    expect(await screen.findByText("This looks like a scam")).toBeInTheDocument();
    const call = api.called("POST", "/api/public/checker/appeal")[0]!;
    expect(call.body).toMatchObject({ image: { type: "image/png" } });
  });

  it("explains when the words in a picture could not be read", async () => {
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": { status: 422, body: { error: { code: "image_unreadable", message: "x" } } } });
    const { container } = renderApp("/check");
    await userEvent.click(await screen.findByRole("tab", { name: "Upload a screenshot" }));
    await userEvent.upload(container.querySelector('input[type="file"]:not([capture])') as HTMLInputElement, new File(["png"], "chat.png", { type: "image/png" }));
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    expect(await screen.findByText(/could not read enough text in that picture/)).toBeInTheDocument();
  });

  it("says how long to wait when rate limited", async () => {
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": { status: 429, headers: { "Retry-After": "1800" }, body: { error: { code: "rate_limited", message: "x" } } } });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("The message, exactly as you received it"), message);
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    expect(await screen.findByText("Too many tries. Please wait 1800 seconds and try again.")).toBeInTheDocument();
  });

  it("lets the person check another message", async () => {
    mockApi({ ...anonymous, "POST /api/public/checker/appeal": result() });
    renderApp("/check");
    await userEvent.type(await screen.findByLabelText("The message, exactly as you received it"), message);
    await userEvent.click(screen.getByRole("button", { name: "Check this appeal" }));
    await userEvent.click(await screen.findByRole("button", { name: "Check another message" }));
    expect(await screen.findByLabelText("The message, exactly as you received it")).toHaveValue("");
  });

  it("is explained as switched off when the feature is off", async () => {
    mockApi({ ...anonymous, "GET /api/public/config": { paypal_client_id: "", paypal_mode: "mock", currency: "USD", demo_banner: false, features: { agent: false, appeal_checker: false } } });
    renderApp("/check");
    expect(await screen.findByText("The appeal checker is switched off")).toBeInTheDocument();
  });
});

describe("validateUpload with images only", () => {
  it("refuses a PDF but accepts PNG and JPEG", () => {
    expect(validateUpload({ name: "a.pdf", type: "application/pdf", size: 10 }, { imagesOnly: true })).toBe("type");
    expect(validateUpload({ name: "a.pdf", type: "", size: 10 }, { imagesOnly: true })).toBe("type");
    expect(validateUpload({ name: "a.png", type: "image/png", size: 10 }, { imagesOnly: true })).toBeNull();
    expect(validateUpload({ name: "a.jpeg", type: "image/jpeg", size: 10 }, { imagesOnly: true })).toBeNull();
    expect(validateUpload({ name: "a.pdf", type: "application/pdf", size: 10 })).toBeNull();
  });
});

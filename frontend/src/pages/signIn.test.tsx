import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { familyUser, mockApi } from "@/test/mockApi";
import { renderApp } from "@/test/renderApp";

const anonymous = { "GET /api/auth/me": { status: 401, body: { error: { code: "unauthenticated", message: "Sign in." } } } };

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

async function enterEmail(email: string) {
  await userEvent.type(await screen.findByLabelText("Email address"), email);
  await userEvent.click(screen.getByRole("button", { name: "Send me a code" }));
}

describe("email code sign-in", () => {
  it("asks for the code, then signs the person in and opens their area", async () => {
    const api = mockApi({
      ...anonymous,
      "POST /api/auth/request-code": { status: 202, body: { message: "sent" } },
      "POST /api/auth/verify": { body: { user: familyUser } },
    });
    const { router } = renderApp("/login");
    await enterEmail("family1@example.test");

    expect(await screen.findByRole("heading", { name: "Enter your code" })).toBeInTheDocument();
    expect(api.called("POST", "/api/auth/request-code")[0]?.body).toEqual({ email: "family1@example.test" });
    expect(api.called("POST", "/api/auth/request-code")[0]?.csrf).toBe("test-token");

    await userEvent.click(screen.getAllByRole("textbox")[0]!);
    await userEvent.keyboard("482913");

    expect(await screen.findByRole("heading", { name: "My campaigns" })).toBeInTheDocument();
    expect(api.called("POST", "/api/auth/verify")).toHaveLength(1);
    expect(api.called("POST", "/api/auth/verify")[0]?.body).toEqual({ email: "family1@example.test", code: "482913" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  });

  it("fetches a fresh CSRF token after signing in, because the session changed", async () => {
    const api = mockApi({
      ...anonymous,
      "POST /api/auth/request-code": { status: 202, body: { message: "sent" } },
      "POST /api/auth/verify": { body: { user: familyUser } },
      "POST /api/auth/logout": { status: 204 },
    });
    const { router } = renderApp("/login");
    await enterEmail("family1@example.test");
    await userEvent.click((await screen.findAllByRole("textbox"))[0]!);
    await userEvent.keyboard("482913");
    await screen.findByRole("heading", { name: "My campaigns" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
    const before = api.called("GET", "/api/auth/csrf").length;
    // Focus moves to the page content just after a navigation, which closes a menu opened in that instant; retry the click.
    await waitFor(async () => {
      const trigger = screen.getByRole("button", { name: /Account menu/ });
      if (trigger.getAttribute("aria-expanded") !== "true") await userEvent.click(trigger);
      expect(screen.getByRole("menuitem", { name: /Sign out/ })).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("menuitem", { name: /Sign out/ }));
    await waitFor(() => expect(api.called("POST", "/api/auth/logout")).toHaveLength(1));
    expect(api.called("GET", "/api/auth/csrf").length).toBe(before + 1);
  });

  it("returns to the page the person was heading for", async () => {
    mockApi({
      ...anonymous,
      "POST /api/auth/request-code": { status: 202, body: { message: "sent" } },
      "POST /api/auth/verify": { body: { user: familyUser } },
    });
    const { router } = renderApp("/app/new");
    // /app/new does not exist yet; the guard on /app still applies to nothing, so go through /login directly.
    router.navigate("/login?next=%2Fapp");
    await enterEmail("family1@example.test");
    await userEvent.click((await screen.findAllByRole("textbox"))[0]!);
    await userEvent.keyboard("111111");
    await screen.findByRole("heading", { name: "My campaigns" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  });

  it("clears the boxes and explains when the code is wrong", async () => {
    mockApi({
      ...anonymous,
      "POST /api/auth/request-code": { status: 202, body: { message: "sent" } },
      "POST /api/auth/verify": { status: 401, body: { error: { code: "invalid_code", message: "Invalid code." } } },
    });
    renderApp("/login");
    await enterEmail("family1@example.test");
    await userEvent.click((await screen.findAllByRole("textbox"))[0]!);
    await userEvent.keyboard("000000");
    expect(await screen.findByText(/not right, or it has expired/)).toBeInTheDocument();
    const boxes = screen.getAllByRole("textbox") as HTMLInputElement[];
    expect(boxes.map((box) => box.value).join("")).toBe("");
  });

  it("says how long to wait when the sign-in is rate limited", async () => {
    mockApi({
      ...anonymous,
      "POST /api/auth/request-code": { status: 429, headers: { "Retry-After": "600" }, body: { error: { code: "rate_limited", message: "Too many." } } },
    });
    renderApp("/login");
    await enterEmail("family1@example.test");
    expect(await screen.findByText("Too many tries. Please wait 600 seconds and try again.")).toBeInTheDocument();
  });

  it("checks the email address before asking the server", async () => {
    const api = mockApi(anonymous);
    renderApp("/login");
    await enterEmail("not-an-email");
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(api.called("POST", "/api/auth/request-code")).toHaveLength(0);
  });

  it("goes back to the email screen if the code page is opened without one", async () => {
    mockApi(anonymous);
    const { router } = renderApp("/login/verify");
    await screen.findByRole("heading", { name: "Sign in" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });

  it("lets a signed-in person skip the sign-in page", async () => {
    mockApi({ "GET /api/auth/me": familyUser });
    const { router } = renderApp("/login");
    await screen.findByRole("heading", { name: "My campaigns" });
    await waitFor(() => expect(router.state.location.pathname).toBe("/app"));
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { initialLanguage } from "./index";

describe("initialLanguage", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("prefers the saved choice", () => {
    localStorage.setItem("tiba.lang", "sw");
    expect(initialLanguage()).toBe("sw");
  });

  it("falls back to the browser language, then English", () => {
    vi.stubGlobal("navigator", { languages: ["sw-KE", "en"], language: "sw-KE" });
    expect(initialLanguage()).toBe("sw");
    vi.stubGlobal("navigator", { languages: ["fr-FR"], language: "fr-FR" });
    expect(initialLanguage()).toBe("en");
  });
});

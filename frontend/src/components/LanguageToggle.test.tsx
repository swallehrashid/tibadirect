import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "@/i18n";
import { LanguageToggle } from "./LanguageToggle";

describe("LanguageToggle", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
    localStorage.clear();
  });

  it("switches the language, remembers it and sets the page language", async () => {
    render(<LanguageToggle />);
    expect(screen.getByRole("button", { name: /EN/ })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: /SW/ }));
    expect(i18n.language).toBe("sw");
    expect(localStorage.getItem("tiba.lang")).toBe("sw");
    expect(document.documentElement.lang).toBe("sw");
    expect(screen.getByRole("button", { name: /SW/ })).toHaveAttribute("aria-pressed", "true");
  });
});

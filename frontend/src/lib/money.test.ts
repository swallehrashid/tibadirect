import { formatKes, formatUsd, minorToInput, parseMinor, percentOf } from "./money";

describe("parseMinor", () => {
  it.each([
    ["25", 2500],
    ["25.5", 2550],
    ["25.50", 2550],
    ["1,250.00", 125000],
    ["0.05", 5],
    [" 10 ", 1000],
  ])("reads %s as %i minor units", (text, minor) => {
    expect(parseMinor(text)).toBe(minor);
  });

  it.each(["", ".", "abc", "1.234", "-5", "1e3", "1.", "12,34,5x"])("rejects %j", (text) => {
    expect(parseMinor(text)).toBeNull();
  });

  it("rejects amounts too large to hold exactly", () => {
    expect(parseMinor("9".repeat(20))).toBeNull();
  });
});

describe("formatting", () => {
  it("drops cents from whole amounts and keeps them otherwise", () => {
    expect(formatKes(1250000)).toBe("KES 12,500");
    expect(formatKes(1250050)).toBe("KES 12,500.50");
    expect(formatUsd(2500)).toBe("$25");
    expect(formatUsd(2550)).toBe("$25.50");
  });

  it("round-trips through the input text", () => {
    expect(minorToInput(2550)).toBe("25.50");
    expect(minorToInput(2500)).toBe("25");
    expect(minorToInput(null)).toBe("");
  });
});

describe("percentOf", () => {
  it("rounds down and stays within 0 to 100", () => {
    expect(percentOf(1, 3)).toBe(33);
    expect(percentOf(5, 3)).toBe(100);
    expect(percentOf(-4, 10)).toBe(0);
    expect(percentOf(5, 0)).toBe(0);
    expect(percentOf(5, null)).toBe(0);
  });
});

import { kesEstimateMinor, MAX_USD_MINOR, MIN_USD_MINOR, validAmount } from "./donation";

describe("validAmount", () => {
  it("accepts $1 to $10,000 inclusive", () => {
    expect(validAmount(MIN_USD_MINOR)).toBe(true);
    expect(validAmount(2500)).toBe(true);
    expect(validAmount(MAX_USD_MINOR)).toBe(true);
  });

  it("rejects nothing, too little and too much", () => {
    expect(validAmount(null)).toBe(false);
    expect(validAmount(0)).toBe(false);
    expect(validAmount(99)).toBe(false);
    expect(validAmount(MAX_USD_MINOR + 1)).toBe(false);
  });
});

describe("kesEstimateMinor", () => {
  it("multiplies cents by the rate and rounds to a whole cent", () => {
    expect(kesEstimateMinor(2500, "129.00000000")).toBe(322500);
    expect(kesEstimateMinor(333, "129.5")).toBe(43124);
  });

  it("gives nothing when the rate is missing or not a usable number", () => {
    expect(kesEstimateMinor(2500, undefined)).toBeNull();
    expect(kesEstimateMinor(2500, "abc")).toBeNull();
    expect(kesEstimateMinor(2500, "0")).toBeNull();
  });
});

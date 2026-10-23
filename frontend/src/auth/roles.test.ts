import { homeFor, safeNext } from "./roles";

describe("homeFor", () => {
  it("sends each role to its own area", () => {
    expect(homeFor("family")).toBe("/app");
    expect(homeFor("hospital_staff")).toBe("/hospital");
    expect(homeFor("admin")).toBe("/admin");
  });
});

describe("safeNext", () => {
  it("follows an address inside the person's own area", () => {
    expect(safeNext("/app/cases/123", "family")).toBe("/app/cases/123");
    expect(safeNext("/hospital?x=1", "hospital_staff")).toBe("/hospital?x=1");
  });

  it("follows a public address", () => {
    expect(safeNext("/c/maria-k", "family")).toBe("/c/maria-k");
    expect(safeNext("/cases", "admin")).toBe("/cases");
  });

  it("falls back to the role's home for another role's area, other sites and the login pages", () => {
    expect(safeNext("/admin/audit", "family")).toBe("/app");
    expect(safeNext("https://evil.example/app", "family")).toBe("/app");
    expect(safeNext("//evil.example", "family")).toBe("/app");
    expect(safeNext("/\\evil.example", "family")).toBe("/app");
    expect(safeNext("/login", "family")).toBe("/app");
    expect(safeNext("/login/verify", "family")).toBe("/app");
    expect(safeNext(null, "admin")).toBe("/admin");
    expect(safeNext("", "admin")).toBe("/admin");
  });

  it("does not treat a lookalike prefix as the person's area", () => {
    expect(safeNext("/application", "family")).toBe("/application"); // a public path that merely starts with the letters
    expect(safeNext("/administrator", "family")).toBe("/administrator");
  });
});

import { describe, expect, it } from "vitest";
import { FEEDBACK_ADDRESS, feedbackHref } from "./feedback";

const labels = { subject: "TibaDirect feedback", body: "What were you trying to do?\nWhat happened?" };

describe("feedback link", () => {
  it("opens an email to the team with the page path and the questions, and nothing personal", () => {
    const url = new URL(feedbackHref("/c/ab12cd34ef56?utm=x&name=Jane", labels));
    expect(url.protocol).toBe("mailto:");
    expect(url.pathname).toBe(FEEDBACK_ADDRESS);
    expect(url.searchParams.get("subject")).toBe("TibaDirect feedback");
    const body = url.searchParams.get("body") ?? "";
    expect(body).toContain("What were you trying to do?");
    expect(body).toContain("/c/ab12cd34ef56");
    expect(body).not.toContain("Jane");
    expect(body).not.toContain("utm");
  });

  it("copes with the home page", () => {
    const body = new URL(feedbackHref("/", labels)).searchParams.get("body") ?? "";
    expect(body.endsWith("---\n/")).toBe(true);
  });
});

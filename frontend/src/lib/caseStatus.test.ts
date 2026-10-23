import i18n from "@/i18n";
import { buildTimeline, nextActionKey } from "./caseStatus";

const t = i18n.t.bind(i18n);
const at = (status: string, day: number) => ({ status, at: `2026-10-${String(day).padStart(2, "0")}T08:00:00Z` }) as never;

describe("buildTimeline", () => {
  it("marks what is done, where the case is now, and what usually comes next", () => {
    const steps = buildTimeline("pending_confirmation", [at("draft", 20), at("pending_triage", 21), at("pending_confirmation", 21)], t);
    expect(steps.map((s) => [s.key, s.state])).toEqual([
      ["draft", "done"],
      ["pending_triage", "done"],
      ["pending_confirmation", "current"],
      ["live", "upcoming"],
      ["funded", "upcoming"],
      ["closed", "upcoming"],
    ]);
    expect(steps[0]?.at).toBe("2026-10-20T08:00:00Z");
  });

  it("shows the review detour only when the case took it", () => {
    const withReview = buildTimeline("in_review", [at("draft", 20), at("pending_triage", 20), at("in_review", 21)], t);
    expect(withReview.map((s) => s.key)).toContain("in_review");
    expect(withReview.find((s) => s.key === "in_review")?.state).toBe("current");
    const without = buildTimeline("live", [at("draft", 20), at("pending_triage", 20), at("pending_confirmation", 21), at("live", 22)], t);
    expect(without.map((s) => s.key)).not.toContain("in_review");
  });

  it("keeps a review that was passed as done", () => {
    const steps = buildTimeline("live", [at("draft", 1), at("pending_triage", 1), at("in_review", 2), at("pending_confirmation", 3), at("live", 4)], t);
    expect(steps.find((s) => s.key === "in_review")?.state).toBe("done");
  });

  it("ends a cancelled or paused case with that fact instead of steps it will not take", () => {
    const cancelled = buildTimeline("cancelled", [at("draft", 1), at("pending_triage", 1), at("pending_confirmation", 2), at("live", 3), at("cancelled", 5)], t);
    expect(cancelled.map((s) => [s.key, s.state])).toEqual([
      ["draft", "done"],
      ["pending_triage", "done"],
      ["pending_confirmation", "done"],
      ["live", "done"],
      ["cancelled", "current"],
    ]);
    const frozen = buildTimeline("frozen", [at("draft", 1), at("pending_triage", 1), at("pending_confirmation", 2), at("live", 3), at("frozen", 4)], t);
    expect(frozen.at(-1)).toMatchObject({ key: "frozen", state: "current" });
  });
});

describe("nextActionKey", () => {
  it("has one message per status, and a special one while a live case is being paid out", () => {
    expect(nextActionKey("draft")).toBe("family.next.draft");
    expect(nextActionKey("live")).toBe("family.next.live");
    expect(nextActionKey("live", true)).toBe("family.next.liveSettling");
  });
});

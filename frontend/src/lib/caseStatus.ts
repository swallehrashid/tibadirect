import type { CaseStatus } from "@/api/enums";
import type { TimelineEntry } from "@/api/models";
import type { TimelineStep } from "@/components/StatusTimeline";

type BadgeTone = "brand" | "confirmed" | "pending" | "info" | "danger" | "neutral";

export const STATUS_TONE: Record<CaseStatus, BadgeTone> = {
  draft: "neutral",
  pending_triage: "info",
  in_review: "pending",
  pending_confirmation: "pending",
  live: "confirmed",
  funded: "confirmed",
  closed: "neutral",
  cancelled: "neutral",
  frozen: "pending",
};

/** The main road a case travels. In-review is a detour that only shows when the case took it. */
const ROAD: CaseStatus[] = ["draft", "pending_triage", "in_review", "pending_confirmation", "live", "funded", "closed"];
const ENDS: CaseStatus[] = ["cancelled", "frozen"];

type Translate = (key: string, options?: Record<string, unknown>) => string;

/**
 * Steps for the family's timeline: what has happened (with dates), where the case is now, and
 * what usually comes next. A case that was cancelled or paused shows its own end instead of a
 * list of steps it will not take.
 */
export function buildTimeline(status: CaseStatus, history: TimelineEntry[], t: Translate): TimelineStep[] {
  const visited = new Map<CaseStatus, string>();
  for (const entry of history) if (!visited.has(entry.status)) visited.set(entry.status, entry.at);

  const ended = ENDS.includes(status);
  const steps: TimelineStep[] = [];
  for (const step of ROAD) {
    const seen = visited.has(step);
    if (step === "in_review" && !seen && status !== "in_review") continue; // a detour, only shown if taken
    if (ended && !seen) continue;
    const state = step === status ? "current" : seen ? "done" : "upcoming";
    steps.push({ key: step, label: t(`timeline.${step}`), at: visited.get(step) ?? null, state });
  }
  if (ended) steps.push({ key: status, label: t(`timeline.${status}`), at: visited.get(status) ?? null, state: "current" });
  return steps;
}

/** What the family should do or expect next, as a translation key. */
export function nextActionKey(status: CaseStatus, hasMoneyToSettle = false): string {
  if (status === "live" && hasMoneyToSettle) return "family.next.liveSettling";
  return `family.next.${status}`;
}

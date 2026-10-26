import { expect, test } from "@playwright/test";
import { Api, caseStatus, kileleBill, startCase, uniqueReference, waitUntil } from "../support/stack";

/**
 * Ten families send a bill at about the same time. Every one is checked (read, redacted, compared with the register
 * and with earlier bills, scored) and moved on within a minute of being sent. Run it against any stack with
 * E2E_BASE_URL; the local stack has one worker.
 */
const FAMILIES = 10;
const BUDGET_MS = 60_000;

test("ten bills sent together are all checked within a minute", async () => {
  test.setTimeout(5 * 60_000);
  const run = uniqueReference().toLowerCase();
  // One after the other: the sign-in screens allow only a few codes at a time from one address.
  const families: Api[] = [];
  for (let index = 0; index < FAMILIES; index++) families.push(await Api.signedIn(`perf.${run}.${index}@example.test`));

  const started = await Promise.all(families.map((family, index) => startCase(family, { alias: `Perf ${index}`, bill: kileleBill({ total: 40_000 + index * 1_000 }) })));

  const finishedAt = new Map<string, number>();
  await waitUntil("every bill to be checked", async () => {
    for (const [index, item] of started.entries()) {
      if (finishedAt.has(item.id)) continue;
      if ((await caseStatus(families[index]!, item.id)) !== "pending_triage") finishedAt.set(item.id, Date.now());
    }
    return finishedAt.size === FAMILIES;
  }, { timeout: 2 * BUDGET_MS, every: 500 });

  const seconds = started.map((item) => (finishedAt.get(item.id)! - item.submittedAt) / 1000).sort((a, b) => a - b);
  const slowest = seconds[seconds.length - 1]!;
  const median = seconds[Math.floor(seconds.length / 2)]!;
  test.info().annotations.push({ type: "triage seconds", description: `median ${median.toFixed(1)}, slowest ${slowest.toFixed(1)}, all ${seconds.map((value) => value.toFixed(1)).join(" ")}` });
  console.log(`triage of ${FAMILIES} bills: median ${median.toFixed(1)} s, slowest ${slowest.toFixed(1)} s`);
  expect(slowest * 1000).toBeLessThan(BUDGET_MS);
  await Promise.all(families.map((family) => family.dispose()));
});

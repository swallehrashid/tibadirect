// Writes docs/i18n/swahili-review.csv: every Swahili string next to its English source, for a native speaker to check.
// Run with: npm run i18n:review   (from the frontend folder). Mark a row "ok" or write the better wording in "reviewer_wording".
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const en = JSON.parse(readFileSync(new URL("../src/i18n/en.json", import.meta.url), "utf8"));
const sw = JSON.parse(readFileSync(new URL("../src/i18n/sw.json", import.meta.url), "utf8"));

function flatten(tree, prefix = "") {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === "string" ? [[path, value]] : flatten(value, path);
  });
}

// Strings people rely on most to understand money, consent or safety: reviewed first.
const PRIORITY = [/^donate\./, /^receipt\./, /^trust\./, /^legal\./, /^checker\./, /^hospital\.decision\./, /^wizard\.consent\./, /^family\.consents\./, /^howItWorks\.excess\./, /^error\./];
const priorityOf = (key) => (PRIORITY.some((pattern) => pattern.test(key)) ? "first" : "later");

const quote = (text) => `"${String(text).replace(/"/g, '""')}"`;
const swahili = new Map(flatten(sw));
const rows = flatten(en).map(([key, english]) => [key, priorityOf(key), english, swahili.get(key) ?? "", "unreviewed", ""]);
rows.sort((a, b) => (a[1] === b[1] ? 0 : a[1] === "first" ? -1 : 1));
const header = ["key", "priority", "english", "swahili", "status", "reviewer_wording"];
const out = [header, ...rows].map((row) => row.map(quote).join(",")).join("\n") + "\n";
const target = new URL("../../docs/i18n/swahili-review.csv", import.meta.url);
mkdirSync(new URL("./", target), { recursive: true });
writeFileSync(target, out);
console.log(`${rows.length} strings written to docs/i18n/swahili-review.csv (${rows.filter((r) => r[1] === "first").length} to review first)`);

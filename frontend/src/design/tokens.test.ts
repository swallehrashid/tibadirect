import { readFileSync } from "node:fs";
import { contrast } from "./contrast";

const tokensCss = readFileSync("src/design/tokens.css", "utf8") // vitest runs from the frontend folder;

function token(name: string): string {
  const match = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(tokensCss);
  if (!match?.[1]) throw new Error(`Token --${name} is not a plain hex colour in tokens.css`);
  return match[1];
}

// [foreground, background, minimum ratio, where it is used]
const TEXT_PAIRS: [string, string, number, string][] = [
  ["tiba-blue-900", "tiba-blue-50", 4.5, "body text on the page wash"],
  ["tiba-blue-900", "surface", 4.5, "text on cards"],
  ["slate-600", "tiba-blue-50", 4.5, "muted text on the page wash"],
  ["slate-600", "surface", 4.5, "muted text on cards"],
  ["slate-600", "slate-100", 4.5, "muted text on muted fills"],
  ["tiba-blue-600", "surface", 4.5, "links on cards"],
  ["tiba-blue-600", "tiba-blue-50", 4.5, "links on the page wash"],
  ["primary-foreground", "tiba-blue-600", 4.5, "primary button label"],
  ["primary-foreground", "tiba-blue-700", 4.5, "primary button label on hover"],
  ["tiba-blue-900", "sky-500", 4.5, "label on the sky fill"],
  ["sky-800", "sky-100", 4.5, "sky chip text"],
  ["uzima-700", "uzima-50", 4.5, "confirmed chip text"],
  ["uzima-700", "uzima-100", 4.5, "confirmed chip text on a deeper tint"],
  ["uzima-700", "surface", 4.5, "teal text on cards"],
  ["tiba-blue-900", "jua-400", 4.5, "label on the amber fill"],
  ["jua-700", "jua-50", 4.5, "pending chip text"],
  ["jua-700", "surface", 4.5, "amber text on cards"],
  ["danger-700", "danger-50", 4.5, "error text"],
  ["danger-600", "surface", 4.5, "error text on cards"],
  ["destructive-foreground", "danger-600", 4.5, "destructive button label"],
];

// Borders and fills that identify a control or carry meaning need 3:1 (WCAG 1.4.11).
const GRAPHIC_PAIRS: [string, string, number, string][] = [
  ["input", "surface", 3, "form control border on cards"],
  ["input", "tiba-blue-50", 3, "form control border on the page wash"],
  ["tiba-blue-600", "surface", 3, "settled bar against its track"],
  ["sky-500", "surface", 2, "pledged bar (decorative, the figures are also written out)"],
  ["uzima-600", "surface", 3, "settled tick"],
];

const aliases: Record<string, string> = {
  "primary-foreground": "#ffffff",
  "destructive-foreground": "#ffffff",
};

function colour(name: string): string {
  return aliases[name] ?? token(name);
}

describe("design tokens", () => {
  it.each(TEXT_PAIRS)("%s on %s has contrast of at least %s (%s)", (fg, bg, minimum) => {
    expect(contrast(colour(fg), colour(bg))).toBeGreaterThanOrEqual(minimum);
  });

  it.each(GRAPHIC_PAIRS)("%s against %s has contrast of at least %s (%s)", (fg, bg, minimum) => {
    expect(contrast(colour(fg), colour(bg))).toBeGreaterThanOrEqual(minimum);
  });

  it("keeps the approved brand values", () => {
    expect(token("tiba-blue-600")).toBe("#1d5fd1");
    expect(token("sky-500")).toBe("#38bdf8");
    expect(token("uzima-600")).toBe("#0e8c7e");
    expect(token("jua-400")).toBe("#f4b400");
  });
});

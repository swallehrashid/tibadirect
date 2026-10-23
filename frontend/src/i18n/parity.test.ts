import en from "./en.json";
import sw from "./sw.json";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[path] = value;
    else Object.assign(out, flatten(value, path));
  }
  return out;
}

const english = flatten(en as Tree);
const swahili = flatten(sw as Tree);
const placeholders = (text: string) => [...text.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((match) => match[1]).sort();

describe("translations", () => {
  it("has every English string in Swahili, and nothing extra", () => {
    const missing = Object.keys(english).filter((key) => !(key in swahili));
    const extra = Object.keys(swahili).filter((key) => !(key in english));
    expect({ missing, extra }).toEqual({ missing: [], extra: [] });
  });

  it("keeps the same {{placeholders}} in both languages", () => {
    const mismatched = Object.keys(english)
      .filter((key) => key in swahili)
      .filter((key) => JSON.stringify(placeholders(english[key]!)) !== JSON.stringify(placeholders(swahili[key]!)));
    expect(mismatched).toEqual([]);
  });

  it("has no empty strings", () => {
    expect(Object.entries(swahili).filter(([, value]) => value.trim() === "")).toEqual([]);
    expect(Object.entries(english).filter(([, value]) => value.trim() === "")).toEqual([]);
  });

  it("has no Swahili string that is just the English one, other than names", () => {
    const allowed = new Set(["app.name", "language.en", "language.sw", "hospital.history.by"]); // names and pure placeholders
    const same = Object.keys(english).filter((key) => english[key] === swahili[key] && !allowed.has(key) && english[key]!.length > 12);
    expect(same).toEqual([]);
  });
});

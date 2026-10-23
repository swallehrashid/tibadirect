import axe from "axe-core";

/**
 * Runs axe on a rendered page and returns what it found, as short readable lines.
 * jsdom has no layout, so colour contrast and scrolling are left to the real-browser run
 * (e2e/accessibility.spec.ts); everything else (names, roles, labels, headings, landmarks) is checked here.
 */
export async function violationsIn(container: Element): Promise<string[]> {
  const results = await axe.run(container, {
    iframes: false, // jsdom cannot message into a frame; the real-browser run covers the bill viewer
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
    rules: {
      "color-contrast": { enabled: false },
      "scrollable-region-focusable": { enabled: false },
      // A component rendered on its own is not a whole page.
      region: { enabled: false },
      "landmark-one-main": { enabled: false },
      "page-has-heading-one": { enabled: false },
    },
  });
  return results.violations.map((violation) => `${violation.id}: ${violation.help} -> ${violation.nodes.map((node) => node.html.slice(0, 100)).join(" | ")}`);
}

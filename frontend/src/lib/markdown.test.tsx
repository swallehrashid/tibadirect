import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LegalBlocks } from "@/components/LegalBlocks";
import { inline, parseDocument } from "./markdown";

describe("notice reader", () => {
  const SOURCE = `# Title here

First line of a paragraph
continues here.

## A heading

- one **bold** item
- two with [a link](https://example.test/x)

Last paragraph.`;

  it("reads the title, headings, paragraphs and lists", () => {
    const doc = parseDocument(SOURCE);
    expect(doc.title).toBe("Title here");
    expect(doc.blocks).toEqual([
      { kind: "p", text: "First line of a paragraph continues here." },
      { kind: "h2", text: "A heading" },
      { kind: "ul", items: ["one **bold** item", "two with [a link](https://example.test/x)"] },
      { kind: "p", text: "Last paragraph." },
    ]);
  });

  it("draws bold text and safe links as elements", () => {
    render(<LegalBlocks blocks={parseDocument(SOURCE).blocks} />);
    expect(screen.getByRole("heading", { level: 2, name: "A heading" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "a link" })).toHaveAttribute("href", "https://example.test/x");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("never turns markup into markup, and drops links that are not https, mailto or on this site", () => {
    render(<p>{inline("<img src=x onerror=alert(1)> [click](javascript:alert(1)) [mail](mailto:a@b.test) [home](/privacy) [net](//evil.test)")}</p>);
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "click" })).toBeNull();
    expect(screen.queryByRole("link", { name: "net" })).toBeNull();
    expect(screen.getByRole("link", { name: "mail" })).toHaveAttribute("href", "mailto:a@b.test");
    expect(screen.getByRole("link", { name: "home" })).toHaveAttribute("href", "/privacy");
    expect(document.body.textContent).toContain("click"); // the words stay, the link does not
  });
});

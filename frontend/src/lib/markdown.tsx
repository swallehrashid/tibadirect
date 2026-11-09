import { Fragment, type ReactNode } from "react";

/**
 * A small reader for the notices in src/legal: one "# title", "## headings", paragraphs, "- " lists,
 * **bold** and [links](https://...). It builds React elements, never HTML strings, so nothing in a
 * notice can run as markup; a link is drawn only if it is https, mailto or a path on this site.
 */
export type Block = { kind: "h2"; text: string } | { kind: "p"; text: string } | { kind: "ul"; items: string[] };

export interface LegalDocument {
  title: string;
  blocks: Block[];
}

export function parseDocument(source: string): LegalDocument {
  let title = "";
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ kind: "p", text: paragraph.join(" ") });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ kind: "ul", items: list });
    list = null;
  };

  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    if (line.startsWith("# ")) {
      title = line.slice(2).trim();
    } else if (line.startsWith("## ")) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "h2", text: line.slice(3).trim() });
    } else if (line.startsWith("- ")) {
      flushParagraph();
      (list ??= []).push(line.slice(2).trim());
    } else if (line.trim() === "") {
      flushParagraph();
      flushList();
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph();
  flushList();
  return { title, blocks };
}

const INLINE = /\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

function safeHref(href: string): string | null {
  return /^(https:\/\/|mailto:|\/(?!\/))/.test(href) ? href : null;
}

export function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) nodes.push(text.slice(last, at));
    const [, bold, label, href] = match;
    if (bold !== undefined) {
      nodes.push(<strong key={at}>{bold}</strong>);
    } else {
      const safe = safeHref(href ?? "");
      nodes.push(
        safe ? (
          <a key={at} href={safe} className="font-semibold text-primary underline underline-offset-4">
            {label}
          </a>
        ) : (
          <Fragment key={at}>{label}</Fragment>
        ),
      );
    }
    last = at + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

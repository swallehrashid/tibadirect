import { inline, type Block } from "@/lib/markdown";

export function LegalBlocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((block, index) => {
        if (block.kind === "h2") {
          return (
            <h2 key={index} className="pt-4 font-heading text-xl font-bold">
              {block.text}
            </h2>
          );
        }
        if (block.kind === "ul") {
          return (
            <ul key={index} className="list-disc space-y-2 pl-6 text-muted-foreground marker:text-primary">
              {block.items.map((item, at) => (
                <li key={at}>{inline(item)}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={index} className="leading-relaxed text-muted-foreground">
            {inline(block.text)}
          </p>
        );
      })}
    </>
  );
}

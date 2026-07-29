import { Fragment } from "react";

const citationPattern = /(seg_\d{6})\s*@\s*([0-9:.]+)[–-]([0-9:.]+)/g;

function RichLine({
  text,
  onCitation,
}: {
  text: string;
  onCitation: (cueId: string) => void;
}) {
  const parts: Array<string | { cueId: string; label: string }> = [];
  let cursor = 0;
  for (const match of text.matchAll(citationPattern)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push(text.slice(cursor, index));
    parts.push({ cueId: match[1], label: match[0] });
    cursor = index + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return (
    <>
      {parts.map((part, index) =>
        typeof part === "string" ? (
          <Fragment key={`${part}-${index}`}>{part}</Fragment>
        ) : (
          <button
            key={`${part.cueId}-${index}`}
            type="button"
            className="citation-link"
            onClick={() => onCitation(part.cueId)}
          >
            {part.label}
          </button>
        ),
      )}
    </>
  );
}

export function MarkdownNote({
  markdown,
  onCitation,
}: {
  markdown: string;
  onCitation: (cueId: string) => void;
}) {
  const content = markdown.replace(/^---[\s\S]*?---\s*/u, "");
  return (
    <article className="note-document">
      {content.split(/\r?\n/u).map((line, index) => {
        if (!line.trim()) return <div className="note-space" key={index} />;
        if (line.startsWith("# ")) {
          return (
            <h2 key={index}>
              <RichLine text={line.slice(2)} onCitation={onCitation} />
            </h2>
          );
        }
        if (line.startsWith("## ")) {
          return (
            <h3 key={index}>
              <RichLine text={line.slice(3)} onCitation={onCitation} />
            </h3>
          );
        }
        if (line.startsWith("- ")) {
          return (
            <p className="note-point" key={index}>
              <span aria-hidden="true">—</span>{" "}
              <RichLine text={line.slice(2)} onCitation={onCitation} />
            </p>
          );
        }
        if (line.startsWith("> ")) {
          return (
            <aside className="note-caution" key={index}>
              <RichLine text={line.slice(2)} onCitation={onCitation} />
            </aside>
          );
        }
        return (
          <p key={index}>
            <RichLine text={line} onCitation={onCitation} />
          </p>
        );
      })}
    </article>
  );
}

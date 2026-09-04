import { Fragment } from "react";

const citationPattern = /\[seg_\d{6}\s*@\s*[0-9:.]+[–-][0-9:.]+\]|\[[0-9:]+\]\(vtnote:\/\/cue\/seg_\d{6}\)/gu;

export function cleanNoteMarkdown(markdown: string): string {
  return markdown
    .replace(/^---[\s\S]*?---\s*/u, "")
    .replace(/^>[ \t]*AI[ \t]*生成内容[：:].*\r?$/gmu, "")
    .replace(citationPattern, "")
    .replace(/[ \t]+$/gmu, "")
    .replace(/\n{3,}/gu, "\n\n")
    .trim();
}

function InlineText({ text }: { text: string }) {
  const pattern = /(\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/gu;
  const parts: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) parts.push(text.slice(cursor, index));
    parts.push(match[0]);
    cursor = index + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
        }
        const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/u);
        if (link) {
          return (
            <a
              key={`${link[2]}-${index}`}
              href={link[2]}
              target="_blank"
              rel="noreferrer"
            >
              {link[1]}
            </a>
          );
        }
        return <Fragment key={`${part}-${index}`}>{part}</Fragment>;
      })}
    </>
  );
}

function RichLine({ text }: { text: string }) {
  return <InlineText text={text} />;
}

export function MarkdownNote({
  markdown,
}: {
  markdown: string;
}) {
  const content = cleanNoteMarkdown(markdown);
  return (
    <article className="note-document">
      {content.split(/\r?\n/u).map((line, index) => {
        if (!line.trim()) return <div className="note-space" key={index} />;
        if (line.startsWith("# ")) {
          return (
            <h2 key={index}>
              <RichLine text={line.slice(2)} />
            </h2>
          );
        }
        if (line.startsWith("## ")) {
          return (
            <h2 key={index}>
              <RichLine text={line.slice(3)} />
            </h2>
          );
        }
        if (line.startsWith("### ")) {
          return (
            <h3 key={index}>
              <RichLine text={line.slice(4)} />
            </h3>
          );
        }
        if (line.startsWith("- ")) {
          return (
            <p className="note-point" key={index}>
              <span aria-hidden="true">•</span>{" "}
              <RichLine text={line.slice(2)} />
            </p>
          );
        }
        if (/^\d+[.)]\s+/u.test(line)) {
          return (
            <p className="note-question" key={index}>
              <RichLine text={line} />
            </p>
          );
        }
        if (/^(?:#[^\s#]+\s*)+$/u.test(line.trim())) {
          return (
            <p className="note-tags" key={index}>
              <RichLine text={line} />
            </p>
          );
        }
        if (line.startsWith("> ")) {
          return (
            <aside className="note-caution" key={index}>
              <RichLine text={line.slice(2)} />
            </aside>
          );
        }
        return (
          <p key={index}>
            <RichLine text={line} />
          </p>
        );
      })}
    </article>
  );
}

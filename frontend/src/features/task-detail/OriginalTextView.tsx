import { useEffect, useMemo, useState } from "react";
import type { TranscriptSegment } from "../../api/types";
import { formatTimestamp } from "../../app/format";
import { ChevronDownIcon } from "../../app/icons";

export interface OriginalChapter {
  id: string;
  index: number;
  startMs: number;
  endMs: number;
  title: string;
  overview: string;
  paragraphs: string[];
  paragraphSegments: TranscriptSegment[][];
}

export interface OriginalChapterRequest {
  id: string;
  revision: number;
}

function compactText(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}

function shorten(value: string, limit: number): string {
  const text = compactText(value);
  return text.length > limit ? `${text.slice(0, limit).trim()}…` : text;
}

function chapterTitle(segments: TranscriptSegment[], index: number): string {
  const opening = compactText(
    segments.slice(0, 3).map((segment) => segment.text).join(" "),
  );
  const sentence = opening.split(/[。！？!?]/u).find(Boolean) ?? opening;
  return shorten(sentence, 26) || `第 ${index + 1} 章`;
}

function chapterParagraphSegments(
  segments: TranscriptSegment[],
): TranscriptSegment[][] {
  const result: TranscriptSegment[][] = [];
  let current: TranscriptSegment[] = [];
  let currentLength = 0;
  let previousEnd = segments[0]?.start_ms ?? 0;
  for (const segment of segments) {
    const text = compactText(segment.text);
    if (!text) continue;
    const gap = segment.start_ms - previousEnd;
    if (current.length > 0 && (currentLength >= 320 || gap > 4_000)) {
      result.push(current);
      current = [];
      currentLength = 0;
    }
    current.push({ ...segment, text });
    currentLength += text.length;
    previousEnd = segment.end_ms;
  }
  if (current.length > 0) result.push(current);
  return result;
}

export function buildOriginalChapters(
  segments: TranscriptSegment[],
): OriginalChapter[] {
  if (!segments.length) return [];
  const lastEnd = Math.max(...segments.map((segment) => segment.end_ms));
  const chapterCount = Math.min(8, Math.max(1, Math.ceil(lastEnd / 300_000)));
  const chapterDuration = Math.max(1, Math.ceil(lastEnd / chapterCount));
  const groups: TranscriptSegment[][] = Array.from(
    { length: chapterCount },
    () => [],
  );
  for (const segment of segments) {
    const bucket = Math.min(
      chapterCount - 1,
      Math.floor(segment.start_ms / chapterDuration),
    );
    groups[bucket].push(segment);
  }
  return groups
    .filter((group) => group.length > 0)
    .map((group, index) => {
      const paragraphSegments = chapterParagraphSegments(group);
      const paragraphs = paragraphSegments.map((paragraph) =>
        paragraph.map((segment) => segment.text).join(" "),
      );
      return {
        id: `original-chapter-${index + 1}`,
        index,
        startMs: group[0].start_ms,
        endMs: group.at(-1)?.end_ms ?? group[0].end_ms,
        title: chapterTitle(group, index),
        overview: shorten(paragraphs.join(" "), 150),
        paragraphs,
        paragraphSegments,
      };
    });
}

export function originalToMarkdown(
  title: string,
  segments: TranscriptSegment[],
): string {
  const sections = buildOriginalChapters(segments).map(
    (chapter) =>
      `## ${formatTimestamp(chapter.startMs)} ${chapter.title}\n\n${chapter.paragraphs.join("\n\n")}`,
  );
  return `# ${title}\n\n${sections.join("\n\n")}`.trim();
}

export function originalToText(segments: TranscriptSegment[]): string {
  return buildOriginalChapters(segments)
    .flatMap((chapter) => chapter.paragraphs)
    .join("\n\n")
    .trim();
}

export function OriginalTextView({
  segments,
  chapterRequest = null,
  onSeek,
}: {
  segments: TranscriptSegment[];
  chapterRequest?: OriginalChapterRequest | null;
  onSeek?: (startMs: number) => void;
}) {
  const chapters = useMemo(() => buildOriginalChapters(segments), [segments]);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(chapters[0] ? [chapters[0].id] : []),
  );

  const toggleChapter = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  useEffect(() => {
    if (
      !chapterRequest ||
      !chapters.some((chapter) => chapter.id === chapterRequest.id)
    ) {
      return;
    }
    setExpanded((current) => {
      if (current.has(chapterRequest.id)) return current;
      const next = new Set(current);
      next.add(chapterRequest.id);
      return next;
    });
    const target = document.getElementById(chapterRequest.id);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
    target?.focus({ preventScroll: true });
  }, [chapterRequest, chapters]);

  return (
    <article className="original-document" aria-label="原文">
      <div className="original-chapters">
        {chapters.map((chapter) => {
          const isExpanded = expanded.has(chapter.id);
          return (
            <section
              className="original-chapter"
              id={chapter.id}
              key={chapter.id}
              tabIndex={-1}
            >
              <header>
                <div>
                  <time>{formatTimestamp(chapter.startMs)}</time>
                  <h3>{chapter.title}</h3>
                </div>
                <button
                  type="button"
                  aria-expanded={isExpanded}
                  onClick={() => toggleChapter(chapter.id)}
                >
                  {isExpanded ? "收起原文" : "展开原文"}
                  <ChevronDownIcon />
                </button>
              </header>
              {!isExpanded && (
                <p className="original-chapter-overview">{chapter.overview}</p>
              )}
              {isExpanded && (
                <div className="original-chapter-body">
                  {chapter.paragraphSegments.map((paragraph, paragraphIndex) => (
                    <p key={`${chapter.id}-${paragraphIndex}`}>
                      {paragraph.map((segment) =>
                        onSeek ? (
                          <button
                            type="button"
                            className="original-sentence"
                            key={segment.id}
                            title={`跳到 ${formatTimestamp(segment.start_ms)}`}
                            onClick={() => onSeek(segment.start_ms)}
                          >
                            {segment.text}
                          </button>
                        ) : (
                          <span key={segment.id}>{segment.text} </span>
                        ),
                      )}
                    </p>
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </article>
  );
}

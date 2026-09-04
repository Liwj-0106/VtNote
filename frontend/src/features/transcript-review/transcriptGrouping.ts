import type { TranscriptSegment } from "../../api/types";
import { formatTimestamp } from "../../app/format";

const MIN_SENTENCE_LENGTH = 6;
const MAX_SENTENCE_LENGTH = 42;
const MAX_SENTENCE_DURATION_MS = 6_000;
const GAP_BREAK_MS = 1_800;
const TERMINAL_PUNCTUATION = /[。！？!?；;…][”’」』）》】]*$/u;

export interface DisplayTranscriptSegment extends TranscriptSegment {
  sourceIds: string[];
}

function normalizeText(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}

function appendFragment(current: string, fragment: string): string {
  if (!current) return fragment;
  const needsSpace = /[A-Za-z0-9]$/u.test(current) && /^[A-Za-z0-9]/u.test(fragment);
  return `${current}${needsSpace ? " " : ""}${fragment}`;
}

function mergeSegments(
  segments: DisplayTranscriptSegment[],
  separator: string,
): DisplayTranscriptSegment {
  const first = segments[0];
  const last = segments.at(-1) ?? first;
  return {
    id: first.id,
    start_ms: first.start_ms,
    end_ms: last.end_ms,
    text: segments.map((segment) => segment.text).join(separator),
    sourceIds: segments.flatMap((segment) => segment.sourceIds),
  };
}

export function buildSemanticTranscriptSegments(
  segments: TranscriptSegment[],
): DisplayTranscriptSegment[] {
  const source = segments
    .map((segment) => ({
      ...segment,
      text: normalizeText(segment.text),
      sourceIds: [segment.id],
    }))
    .filter((segment) => segment.text.length > 0);
  const result: DisplayTranscriptSegment[] = [];
  let current: DisplayTranscriptSegment[] = [];
  let currentText = "";

  const flush = () => {
    if (!current.length) return;
    result.push({
      ...mergeSegments(current, ""),
      text: currentText,
    });
    current = [];
    currentText = "";
  };

  source.forEach((segment, index) => {
    current.push(segment);
    currentText = appendFragment(currentText, segment.text);
    const next = source[index + 1];
    const duration = segment.end_ms - current[0].start_ms;
    const hasCompleteSentence =
      currentText.length >= MIN_SENTENCE_LENGTH &&
      TERMINAL_PUNCTUATION.test(currentText);
    const nextHasLargeGap = Boolean(
      next && next.start_ms - segment.end_ms > GAP_BREAK_MS,
    );
    if (
      hasCompleteSentence ||
      currentText.length >= MAX_SENTENCE_LENGTH ||
      duration >= MAX_SENTENCE_DURATION_MS ||
      nextHasLargeGap
    ) {
      flush();
    }
  });
  flush();
  return result;
}

export function groupTranscriptSegments(
  segments: TranscriptSegment[],
  requestedGroupSize: number,
): DisplayTranscriptSegment[] {
  const semanticSegments = buildSemanticTranscriptSegments(segments);
  const groupSize = Math.min(20, Math.max(1, Math.round(requestedGroupSize)));
  const result: DisplayTranscriptSegment[] = [];
  for (let index = 0; index < semanticSegments.length; index += groupSize) {
    result.push(
      mergeSegments(semanticSegments.slice(index, index + groupSize), " "),
    );
  }
  return result;
}

export function transcriptToText(segments: TranscriptSegment[]): string {
  return buildSemanticTranscriptSegments(segments)
    .map((segment) => segment.text)
    .join("\n")
    .trim();
}

export function transcriptToMarkdown(
  title: string,
  segments: TranscriptSegment[],
): string {
  const body = buildSemanticTranscriptSegments(segments)
    .map((segment) => `${formatTimestamp(segment.start_ms)}  ${segment.text}`)
    .join("\n\n");
  return `# ${title}\n\n${body}`.trim();
}

function subtitleTimestamp(value: number, separator: "," | "."): string {
  const total = Math.max(0, Math.round(value));
  const milliseconds = total % 1000;
  const seconds = Math.floor(total / 1000) % 60;
  const minutes = Math.floor(total / 60_000) % 60;
  const hours = Math.floor(total / 3_600_000);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}${separator}${String(milliseconds).padStart(3, "0")}`;
}

export function transcriptToSrt(segments: TranscriptSegment[]): string {
  return buildSemanticTranscriptSegments(segments)
    .map(
      (segment, index) =>
        `${index + 1}\n${subtitleTimestamp(segment.start_ms, ",")} --> ${subtitleTimestamp(segment.end_ms, ",")}\n${segment.text}`,
    )
    .join("\n\n");
}

export function transcriptToVtt(segments: TranscriptSegment[]): string {
  const body = buildSemanticTranscriptSegments(segments)
    .map(
      (segment) =>
        `${subtitleTimestamp(segment.start_ms, ".")} --> ${subtitleTimestamp(segment.end_ms, ".")}\n${segment.text}`,
    )
    .join("\n\n");
  return `WEBVTT\n\n${body}`.trim();
}

export function transcriptToJson(segments: TranscriptSegment[]): string {
  return JSON.stringify(
    buildSemanticTranscriptSegments(segments).map((segment) => ({
      id: segment.id,
      start_ms: segment.start_ms,
      end_ms: segment.end_ms,
      text: segment.text,
    })),
    null,
    2,
  );
}

export function transcriptToTimestampedText(
  segments: TranscriptSegment[],
): string {
  return buildSemanticTranscriptSegments(segments)
    .map((segment) => `${formatTimestamp(segment.start_ms)}  ${segment.text}`)
    .join("\n");
}

export function transcriptToLrc(segments: TranscriptSegment[]): string {
  return buildSemanticTranscriptSegments(segments)
    .map((segment) => {
      const totalCentiseconds = Math.max(0, Math.floor(segment.start_ms / 10));
      const minutes = Math.floor(totalCentiseconds / 6_000);
      const seconds = Math.floor(totalCentiseconds / 100) % 60;
      const centiseconds = totalCentiseconds % 100;
      return `[${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(centiseconds).padStart(2, "0")}]${segment.text}`;
    })
    .join("\n");
}

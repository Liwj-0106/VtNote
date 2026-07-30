import { useEffect, useMemo, useRef, useState } from "react";
import type { TranscriptSegment } from "../api/types";
import { formatTimestamp } from "../app/format";

const PAGE_SIZE = 160;

export function TranscriptViewer({
  segments,
  highlightedCueId,
}: {
  segments: TranscriptSegment[];
  highlightedCueId?: string | null;
}) {
  const [fullText, setFullText] = useState(false);
  const [page, setPage] = useState(0);
  const focusAfterRender = useRef<string | null>(null);
  const pages = Math.max(1, Math.ceil(segments.length / PAGE_SIZE));

  useEffect(() => {
    if (!highlightedCueId) return;
    const index = segments.findIndex((segment) => segment.id === highlightedCueId);
    if (index < 0) return;
    if (!fullText) setPage(Math.floor(index / PAGE_SIZE));
    focusAfterRender.current = highlightedCueId;
  }, [highlightedCueId, segments, fullText]);

  const visible = useMemo(
    () =>
      fullText
        ? segments
        : segments.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [fullText, page, segments],
  );

  useEffect(() => {
    const cueId = focusAfterRender.current;
    if (!cueId) return;
    const element = document.getElementById(cueId);
    if (element) {
      element.focus({ preventScroll: true });
      element.scrollIntoView({ block: "center", behavior: "smooth" });
      focusAfterRender.current = null;
    }
  }, [visible]);

  return (
    <div className="transcript-viewer">
      <div className="reader-tools">
        <span>
          {segments.length} 条字幕
          {!fullText && pages > 1 ? ` · 第 ${page + 1}/${pages} 页` : ""}
        </span>
        <button
          type="button"
          className="button button-quiet"
          aria-label={
            fullText ? "切换到分页阅读模式" : "切换到完整文本模式"
          }
          onClick={() => {
            setFullText((current) => !current);
            setPage(0);
          }}
        >
          {fullText ? "分页阅读" : "完整文本"}
        </button>
      </div>
      <div
        className={`transcript-list ${fullText ? "is-full-text" : ""}`}
        aria-label="字幕正文"
      >
        {visible.map((segment) => (
          <article
            key={segment.id}
            id={segment.id}
            className={`transcript-cue ${
              highlightedCueId === segment.id ? "is-highlighted" : ""
            }`}
            data-testid="transcript-cue"
            tabIndex={-1}
          >
            <time dateTime={`PT${segment.start_ms / 1000}S`}>
              {formatTimestamp(segment.start_ms)}
            </time>
            <p>{segment.text}</p>
          </article>
        ))}
      </div>
      {!fullText && pages > 1 && (
        <nav className="reader-pagination" aria-label="字幕分页">
          <button
            type="button"
            className="button"
            disabled={page === 0}
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            上一页
          </button>
          <button
            type="button"
            className="button"
            disabled={page >= pages - 1}
            onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}
          >
            下一页
          </button>
        </nav>
      )}
    </div>
  );
}

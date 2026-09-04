import {
  Fragment,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { TranscriptSegment } from "../api/types";
import { formatTimestamp } from "../app/format";
import { BookmarkIcon, ChevronIcon } from "../app/icons";
import { SearchField } from "./SearchField";

const PAGE_SIZE = 160;

function includesCue(segment: TranscriptSegment, cueId: string): boolean {
  const sourceIds = (segment as TranscriptSegment & { sourceIds?: string[] })
    .sourceIds;
  return segment.id === cueId || sourceIds?.includes(cueId) === true;
}

function highlightedText(text: string, query: string) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return text;
  const normalized = text.toLocaleLowerCase();
  const parts: Array<{ text: string; match: boolean }> = [];
  let cursor = 0;
  while (cursor < text.length) {
    const index = normalized.indexOf(needle, cursor);
    if (index < 0) {
      parts.push({ text: text.slice(cursor), match: false });
      break;
    }
    if (index > cursor) {
      parts.push({ text: text.slice(cursor, index), match: false });
    }
    parts.push({ text: text.slice(index, index + needle.length), match: true });
    cursor = index + needle.length;
  }
  return parts.map((part, index) =>
    part.match ? (
      <mark key={`${index}-${part.text}`}>{part.text}</mark>
    ) : (
      <Fragment key={`${index}-${part.text}`}>{part.text}</Fragment>
    ),
  );
}

export function TranscriptViewer({
  segments,
  highlightedCueId,
  activeCueId,
  speakerBySegment,
  excerptSegmentIds,
  onSeek,
  onToggleExcerpt,
  variant = "timeline",
  scrollEnabled = true,
}: {
  segments: TranscriptSegment[];
  highlightedCueId?: string | null;
  activeCueId?: string | null;
  speakerBySegment?: ReadonlyMap<string, string>;
  excerptSegmentIds?: ReadonlySet<string>;
  onSeek?: (startMs: number) => void;
  onToggleExcerpt?: (segment: TranscriptSegment) => void;
  variant?: "timeline" | "cards";
  scrollEnabled?: boolean;
}) {
  const [fullText, setFullText] = useState(false);
  const [page, setPage] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchIndex, setSearchIndex] = useState(0);
  const deferredQuery = useDeferredValue(searchQuery.trim());
  const revealAfterRender = useRef<{ id: string; focus: boolean } | null>(null);
  const searchInput = useRef<HTMLInputElement | null>(null);
  const cardMode = variant === "cards";
  const showAll = cardMode || fullText;
  const pages = Math.max(1, Math.ceil(segments.length / PAGE_SIZE));

  const searchMatches = useMemo(() => {
    const needle = deferredQuery.toLocaleLowerCase();
    if (!needle) return [];
    return segments.filter((segment) =>
      segment.text.toLocaleLowerCase().includes(needle),
    );
  }, [deferredQuery, segments]);
  const normalizedSearchIndex =
    searchMatches.length === 0
      ? 0
      : Math.min(searchIndex, searchMatches.length - 1);
  const searchCueId = searchMatches[normalizedSearchIndex]?.id ?? null;

  useEffect(() => setSearchIndex(0), [deferredQuery]);

  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLocaleLowerCase() !== "f") {
        return;
      }
      event.preventDefault();
      searchInput.current?.focus();
      searchInput.current?.select();
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  useEffect(() => {
    if (!highlightedCueId) return;
    const index = segments.findIndex((segment) =>
      includesCue(segment, highlightedCueId),
    );
    if (index < 0) return;
    if (!showAll) setPage(Math.floor(index / PAGE_SIZE));
    revealAfterRender.current = { id: segments[index].id, focus: true };
  }, [highlightedCueId, segments, showAll]);

  useEffect(() => {
    if (!searchCueId) return;
    const index = segments.findIndex((segment) => segment.id === searchCueId);
    if (index < 0) return;
    if (!showAll) setPage(Math.floor(index / PAGE_SIZE));
    revealAfterRender.current = { id: searchCueId, focus: false };
  }, [searchCueId, segments, showAll]);

  useEffect(() => {
    if (!activeCueId) return;
    const index = segments.findIndex((segment) =>
      includesCue(segment, activeCueId),
    );
    if (index < 0) return;
    if (!showAll) setPage(Math.floor(index / PAGE_SIZE));
    if (scrollEnabled) {
      revealAfterRender.current = { id: segments[index].id, focus: false };
    }
  }, [activeCueId, scrollEnabled, segments, showAll]);

  const visible = useMemo(
    () =>
      showAll
        ? segments
        : segments.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [page, segments, showAll],
  );

  useEffect(() => {
    const target = revealAfterRender.current;
    if (!target) return;
    const element = document.getElementById(target.id);
    if (element) {
      if (target.focus) element.focus({ preventScroll: true });
      element.scrollIntoView?.({ block: "center", behavior: "smooth" });
      revealAfterRender.current = null;
    }
  }, [visible]);

  const moveSearch = (direction: 1 | -1) => {
    if (searchMatches.length === 0) return;
    setSearchIndex(
      (normalizedSearchIndex + direction + searchMatches.length) %
        searchMatches.length,
    );
  };

  return (
    <div className={`transcript-viewer is-${variant}-mode`}>
      <div className={`reader-tools${cardMode ? " is-card-mode" : ""}`}>
        <span>
          {segments.length} 条字幕
          {!showAll && pages > 1 ? ` · 第 ${page + 1}/${pages} 页` : ""}
        </span>
        <div className="reader-actions">
          <SearchField
            ref={searchInput}
            className="transcript-search"
            label="搜索字幕"
            clearLabel="清除字幕搜索"
            value={searchQuery}
            placeholder="搜索字幕"
            onChange={(event) => setSearchQuery(event.currentTarget.value)}
            onClear={() => setSearchQuery("")}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              moveSearch(event.shiftKey ? -1 : 1);
            }}
          />
          {deferredQuery && (
            <span className="transcript-search-count" aria-live="polite">
              {searchMatches.length > 0 ? normalizedSearchIndex + 1 : 0}/
              {searchMatches.length}
            </span>
          )}
          {deferredQuery && (
            <div className="transcript-search-nav">
              <button
                type="button"
                aria-label="上一个匹配项"
                disabled={searchMatches.length === 0}
                onClick={() => moveSearch(-1)}
              >
                <ChevronIcon />
              </button>
              <button
                type="button"
                aria-label="下一个匹配项"
                disabled={searchMatches.length === 0}
                onClick={() => moveSearch(1)}
              >
                <ChevronIcon />
              </button>
            </div>
          )}
          {!cardMode && (
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
          )}
        </div>
      </div>
      <div
        className={`transcript-list ${showAll ? "is-full-text" : ""}`}
        aria-label="字幕正文"
      >
        {visible.map((segment) => (
          <article
            key={segment.id}
            id={segment.id}
            className={`transcript-cue ${
              (highlightedCueId && includesCue(segment, highlightedCueId)) ||
              searchCueId === segment.id
                ? "is-highlighted"
                : ""
            } ${
              activeCueId && includesCue(segment, activeCueId)
                ? "is-playing"
                : ""
            }`}
            data-testid="transcript-cue"
            tabIndex={onToggleExcerpt ? 0 : -1}
            onKeyDown={(event) => {
              if (event.key.toLocaleLowerCase() === "b" && onToggleExcerpt) {
                event.preventDefault();
                onToggleExcerpt(segment);
              }
            }}
          >
            {onSeek ? (
              <button
                type="button"
                className="cue-time"
                aria-label={`从 ${formatTimestamp(segment.start_ms)} 播放`}
                onClick={() => onSeek(segment.start_ms)}
              >
                {formatTimestamp(segment.start_ms)}
              </button>
            ) : (
              <time dateTime={`PT${segment.start_ms / 1000}S`}>
                {formatTimestamp(segment.start_ms)}
              </time>
            )}
            {onSeek ? (
              <button
                type="button"
                className="cue-text"
                title={`跳到 ${formatTimestamp(segment.start_ms)}`}
                onClick={() => onSeek(segment.start_ms)}
              >
                {speakerBySegment?.get(segment.id) && (
                  <span className="speaker-label">
                    {speakerBySegment
                      .get(segment.id)
                      ?.replace(/^speaker_0*/, "说话人 ")}
                  </span>
                )}
                {highlightedText(segment.text, deferredQuery)}
              </button>
            ) : (
              <p>
                {speakerBySegment?.get(segment.id) && (
                  <span className="speaker-label">
                    {speakerBySegment
                      .get(segment.id)
                      ?.replace(/^speaker_0*/, "说话人 ")}
                  </span>
                )}
                {highlightedText(segment.text, deferredQuery)}
              </p>
            )}
            {onToggleExcerpt && (
              <button
                type="button"
                className={`cue-bookmark${excerptSegmentIds?.has(segment.id) ? " is-active" : ""}`}
                aria-label={excerptSegmentIds?.has(segment.id) ? "移除摘录" : "添加摘录"}
                aria-pressed={excerptSegmentIds?.has(segment.id) ?? false}
                onClick={() => onToggleExcerpt(segment)}
              >
                <BookmarkIcon />
              </button>
            )}
          </article>
        ))}
      </div>
      {!showAll && pages > 1 && (
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

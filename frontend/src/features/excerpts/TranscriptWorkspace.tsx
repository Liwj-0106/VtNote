import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type {
  LibraryExcerpt,
  TranscriptSegment,
} from "../../api/types";
import { formatTimestamp } from "../../app/format";
import { TrashIcon } from "../../app/icons";
import { InlineNotice } from "../../components/InlineNotice";
import { MotionPresence } from "../../components/MotionPresence";
import { TranscriptViewer } from "../../components/TranscriptViewer";
import {
  TranscriptPlayer,
  type TranscriptPlayerHandle,
} from "../transcript-review/TranscriptPlayer";

export function TranscriptWorkspace({
  itemId,
  segments,
  highlightedCueId,
  speakerBySegment,
}: {
  itemId: string;
  segments: TranscriptSegment[];
  highlightedCueId?: string | null;
  speakerBySegment?: ReadonlyMap<string, string>;
}) {
  const [excerpts, setExcerpts] = useState<LibraryExcerpt[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [removingExcerptIds, setRemovingExcerptIds] = useState<Set<string>>(new Set());
  const [activeCueId, setActiveCueId] = useState<string | null>(null);
  const [audioAvailable, setAudioAvailable] = useState(false);
  const player = useRef<TranscriptPlayerHandle | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api.request<LibraryExcerpt[]>(`/api/items/${itemId}/excerpts`, {
      signal: controller.signal,
    }).then(setExcerpts).catch((caught: unknown) => {
      if (!controller.signal.aborted) {
        setError(caught instanceof ApiError ? caught.message : "无法读取摘录。");
      }
    });
    return () => controller.abort();
  }, [itemId]);

  const excerptIds = useMemo(
    () => new Set(excerpts.map((excerpt) => excerpt.segment_id)),
    [excerpts],
  );

  const toggleExcerpt = async (segment: TranscriptSegment) => {
    setError(null);
    const existing = excerpts.find((excerpt) => excerpt.segment_id === segment.id);
    try {
      if (existing) {
        await api.request<void>(`/api/excerpts/${existing.id}`, { method: "DELETE" });
        setRemovingExcerptIds((current) => new Set(current).add(existing.id));
      } else {
        const created = await api.request<LibraryExcerpt>(
          `/api/items/${itemId}/excerpts`,
          { method: "POST", body: { segment_id: segment.id } },
        );
        setExcerpts((current) => [...current, created].sort((a, b) => a.start_ms - b.start_ms));
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法更新摘录。");
    }
  };

  const finishExcerptRemoval = (excerptId: string) => {
    setExcerpts((current) => current.filter((excerpt) => excerpt.id !== excerptId));
    setRemovingExcerptIds((current) => {
      const next = new Set(current);
      next.delete(excerptId);
      return next;
    });
  };

  const updateNote = async (excerpt: LibraryExcerpt, note: string) => {
    if ((excerpt.note ?? "") === note.trim()) return;
    try {
      const updated = await api.request<LibraryExcerpt>(`/api/excerpts/${excerpt.id}`, {
        method: "PATCH",
        body: { note },
      });
      setExcerpts((current) => current.map((row) => row.id === updated.id ? updated : row));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法保存摘录笔记。");
    }
  };

  return (
    <div className="transcript-workspace">
      <div>
        <TranscriptPlayer
          ref={player}
          itemId={itemId}
          segments={segments}
          onActiveSegmentChange={setActiveCueId}
          onAvailabilityChange={setAudioAvailable}
        />
        <MotionPresence present={Boolean(error)}>
          {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        </MotionPresence>
        <TranscriptViewer
          segments={segments}
          highlightedCueId={highlightedCueId}
          activeCueId={activeCueId}
          speakerBySegment={speakerBySegment}
          excerptSegmentIds={excerptIds}
          onSeek={audioAvailable ? (startMs) => player.current?.seekTo(startMs) : undefined}
          onToggleExcerpt={(segment) => void toggleExcerpt(segment)}
        />
      </div>
      <aside className="excerpt-panel" aria-label="摘录">
        <h3>摘录</h3>
        {excerpts.length === 0 ? (
          <p>在字幕行添加书签。</p>
        ) : (
          excerpts.map((excerpt) => (
            <MotionPresence
              key={excerpt.id}
              present={!removingExcerptIds.has(excerpt.id)}
              initial={false}
              onExited={() => finishExcerptRemoval(excerpt.id)}
            >
            <article className={excerpt.stale ? "is-stale" : ""}>
              <header>
                <time>{formatTimestamp(excerpt.start_ms)}</time>
                <button
                  type="button"
                  className="icon-button destructive-icon-button"
                  aria-label="移除摘录"
                  onClick={() => {
                    const segment = segments.find((row) => row.id === excerpt.segment_id);
                    if (segment) void toggleExcerpt(segment);
                  }}
                >
                  <TrashIcon />
                </button>
              </header>
              <p>{excerpt.text}</p>
              <textarea
                aria-label="摘录笔记"
                defaultValue={excerpt.note ?? ""}
                rows={2}
                onBlur={(event) => void updateNote(excerpt, event.target.value)}
              />
            </article>
            </MotionPresence>
          ))
        )}
      </aside>
    </div>
  );
}

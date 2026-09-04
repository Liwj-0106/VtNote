import {
  useEffect,
  useState,
  type AnimationEventHandler,
  type CSSProperties,
  type MouseEvent,
} from "react";

import { api, isTerminalStatus } from "../../api/client";
import type { LibraryEntity, SourceProbe, Task } from "../../api/types";
import { formatDate } from "../../app/format";
import {
  DownloadIcon,
  ExternalIcon,
  MoreIcon,
  RefreshIcon,
  TrashIcon,
} from "../../app/icons";
import { AppLink } from "../../app/router";
import { DropdownMenu } from "../../components/DropdownMenu";
import { Skeleton } from "../../components/Skeleton";
import {
  hasFailedSummary,
  hasExportableOutcome,
  taskRetryPlan,
  taskProgress,
  taskTerminalLabel,
  taskTitle,
} from "./model";
import type { LibraryProperty, LibraryViewMode } from "./types";

interface TaskLibraryRowProps {
  task: Task;
  selected: boolean;
  view: LibraryViewMode;
  properties: Set<LibraryProperty>;
  onSelect: (
    taskId: string,
    options?: { shift?: boolean; additive?: boolean; toggle?: boolean },
  ) => void;
  onExport: (task: Task) => void;
  onDelete: (task: Task) => void;
  onRetry?: (task: Task) => void;
  deleteLabel?: string;
  collections?: LibraryEntity[];
  tags?: LibraryEntity[];
  "aria-hidden"?: boolean;
  "data-motion-presence"?: "enter" | "exit";
  inert?: boolean;
  onAnimationEnd?: AnimationEventHandler<HTMLElement>;
}

const sourceMetadataCache = new Map<string, SourceProbe>();

function publicVideoUrl(task: Task): string | null {
  const item = task.items[0];
  if (!item || !["url", "bilibili", "douyin", "youtube"].includes(item.source_kind)) {
    return null;
  }
  try {
    const url = new URL(item.source_locator);
    if (url.protocol !== "https:") return null;
    const host = url.hostname.toLocaleLowerCase();
    const path = url.pathname;
    if (
      ["bilibili.com", "www.bilibili.com"].includes(host) &&
      /^\/video\/(?:BV[0-9A-Za-z]{10}|av[0-9]+)\/?$/u.test(path)
    ) {
      return item.source_locator;
    }
    if (host === "b23.tv" && /^\/[0-9A-Za-z_-]{4,128}\/?$/u.test(path)) {
      return item.source_locator;
    }
    if (host === "youtu.be" && /^\/[0-9A-Za-z_-]{11}\/?$/u.test(path)) {
      return item.source_locator;
    }
    if (
      ["youtube.com", "www.youtube.com"].includes(host) &&
      ((path === "/watch" && /^[0-9A-Za-z_-]{11}$/u.test(url.searchParams.get("v") ?? "")) ||
        /^\/(?:shorts|live)\/[0-9A-Za-z_-]{11}\/?$/u.test(path))
    ) {
      return item.source_locator;
    }
    if (
      ["douyin.com", "www.douyin.com"].includes(host) &&
      /^\/video\/[0-9]{10,24}\/?$/u.test(path)
    ) {
      return item.source_locator;
    }
    if (host === "v.douyin.com" && /^\/[0-9A-Za-z_-]{4,128}\/?$/u.test(path)) {
      return item.source_locator;
    }
  } catch {
    return null;
  }
  return null;
}

function useSourceMetadata(task: Task) {
  const sourceUrl = publicVideoUrl(task);
  const cached = sourceUrl ? sourceMetadataCache.get(sourceUrl) ?? null : null;
  const [metadata, setMetadata] = useState<SourceProbe | null>(cached);
  const [resolved, setResolved] = useState(!sourceUrl || cached !== null);

  useEffect(() => {
    if (!sourceUrl) {
      setMetadata(null);
      setResolved(true);
      return;
    }
    const current = sourceMetadataCache.get(sourceUrl);
    if (current) {
      setMetadata(current);
      setResolved(true);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setMetadata(null);
    setResolved(false);
    void api
      .request<SourceProbe>("/api/sources/probe", {
        method: "POST",
        body: { url: sourceUrl },
        signal: controller.signal,
      })
      .then((result) => {
        if (!active || result.result_type !== "single") return;
        sourceMetadataCache.set(sourceUrl, result);
        setMetadata(result);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setResolved(true);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [sourceUrl]);

  return { metadata, resolved, remote: sourceUrl !== null };
}

function publishedAt(task: Task, metadata: SourceProbe | null): string | null {
  const item = task.items[0];
  const snapshotSource = task.pipeline_snapshot.source;
  const snapshotPublishedAt =
    typeof snapshotSource === "object" &&
    snapshotSource !== null &&
    "published_at" in snapshotSource &&
    typeof snapshotSource.published_at === "string"
      ? snapshotSource.published_at
      : null;
  return item?.published_at ?? snapshotPublishedAt ?? metadata?.published_at ?? null;
}

function summaryAt(task: Task): string {
  const notesRuns = (task.items[0]?.stage_runs ?? [])
    .filter((run) => run.stage === "notes")
    .sort((left, right) => right.attempt - left.attempt);
  return notesRuns[0]?.finished_at ?? task.updated_at;
}

function formatPublishedDate(value: string | null): string {
  if (!value) return "未提供";
  if (/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/u.test(value)) return value;
  return formatDate(value);
}

export function taskLibraryColumnTemplate(properties: Set<LibraryProperty>): string {
  const columns = ["18px"];
  if (properties.has("cover")) columns.push("clamp(116px, 13vw, 176px)");
  if (properties.has("title")) columns.push("minmax(280px, 1fr)");
  if (properties.has("publishedAt")) columns.push("minmax(116px, 150px)");
  columns.push("42px");
  return columns.join(" ");
}

export function TaskLibraryRow({
  task,
  selected,
  view,
  properties,
  onSelect,
  onExport,
  onDelete,
  onRetry,
  deleteLabel = "删除",
  collections = [],
  tags = [],
  ...motionProps
}: TaskLibraryRowProps) {
  const progress = taskProgress(task);
  const exportReady = hasExportableOutcome(task);
  const retryable = onRetry !== undefined && taskRetryPlan(task) !== null;
  const selectable = isTerminalStatus(task.status);
  const title = taskTitle(task);
  const summaryFailed = hasFailedSummary(task);
  const item = task.items[0];
  const { metadata, resolved: metadataResolved, remote } = useSourceMetadata(task);
  const coverUrl = metadata?.thumbnail_url
    ? `/api/sources/thumbnail?url=${encodeURIComponent(metadata.canonical_url)}`
    : item?.thumbnail_url ?? null;
  const [failedCoverUrl, setFailedCoverUrl] = useState<string | null>(null);
  const coverVisible = coverUrl !== null && coverUrl !== failedCoverUrl;
  const author = metadata?.author ?? (remote ? "博主信息未提供" : "本地文件");
  const published = publishedAt(task, metadata);

  const selectFromPointer = (event: MouseEvent<HTMLElement>, toggle: boolean) => {
    onSelect(task.id, {
      shift: event.shiftKey,
      additive: event.ctrlKey || event.metaKey,
      toggle,
    });
  };

  return (
    <article
      {...motionProps}
      className={`library-row is-${view}-row${selectable ? " is-selectable" : ""}${selected ? " is-selected" : ""}`}
      style={
        {
          "--library-row-columns": taskLibraryColumnTemplate(properties),
        } as CSSProperties
      }
      onClick={(event) => {
        if (!selectable) return;
        const target = event.target as HTMLElement;
        if (target.closest("a, button, input, label")) return;
        selectFromPointer(event, event.ctrlKey || event.metaKey);
      }}
    >
      <input
        className="library-select-checkbox"
        type="checkbox"
        checked={selected}
        disabled={!selectable}
        aria-label={`选择${title}`}
        onChange={() => undefined}
        onClick={(event) => selectFromPointer(event, true)}
      />

      {properties.has("cover") && (
        <div
          className={`library-cover${coverVisible ? " has-image" : ""}`}
          aria-busy={!metadataResolved}
        >
          <span aria-hidden="true">Vt</span>
          {!metadataResolved && <Skeleton className="library-cover-loading is-block" />}
          {coverVisible ? (
            <img
              src={coverUrl}
              alt=""
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
              onError={() => setFailedCoverUrl(coverUrl)}
            />
          ) : null}
        </div>
      )}

      {properties.has("title") && (
        <div className="library-record">
          <AppLink
            className="library-title-link"
            to={`/tasks/${task.id}${summaryFailed ? "?tab=notes" : ""}`}
          >
            <h2>{title}</h2>
          </AppLink>
          <div className="library-record-byline">
            {metadataResolved ? (
              <span>{author}</span>
            ) : (
              <Skeleton className="library-inline-metadata-skeleton" />
            )}
          </div>
          {progress || summaryFailed || ["failed", "canceled"].includes(task.status) ? (
            <div className="library-record-meta">
              {progress ? (
                <span>{progress.label}</span>
              ) : (
                <span className={`library-status-text is-${summaryFailed ? "failed" : task.status}`}>
                  {taskTerminalLabel(task)}
                </span>
              )}
              {retryable ? (
                <button
                  type="button"
                  className="library-inline-retry"
                  aria-label={`重试${title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onRetry?.(task);
                  }}
                >
                  <RefreshIcon />
                  重试
                </button>
              ) : null}
            </div>
          ) : null}
          {progress && (
            <div
              className={`library-progress-track${progress.determinate ? "" : " is-indeterminate"}`}
              role="progressbar"
              aria-label={`${title}处理进度`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.determinate ? Math.round(progress.ratio * 100) : undefined}
              aria-valuetext={`${progress.label}${
                progress.determinate ? `，${Math.round(progress.ratio * 100)}%` : ""
              }`}
            >
              <span
                className="library-progress-fill"
                style={{ "--progress-ratio": progress.ratio } as CSSProperties}
              />
              {!progress.determinate && <span className="library-progress-activity" aria-hidden="true" />}
            </div>
          )}
          {(collections.length > 0 || tags.length > 0) && (
            <div className="library-entities" aria-label="合集与标签">
              {[...collections, ...tags].map((entity) => (
                <span key={entity.id}>{entity.name}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {properties.has("publishedAt") && (
        <div className="library-time-stack">
          <div className="library-published-at">
            <span>发布时间</span>
            {metadataResolved ? (
              <time>{formatPublishedDate(published)}</time>
            ) : (
              <Skeleton className="library-inline-date-skeleton" />
            )}
          </div>
          <div className="library-published-at">
            <span>总结时间</span>
            <time>{formatDate(summaryAt(task))}</time>
          </div>
        </div>
      )}

      <DropdownMenu
        ariaLabel={`${title}操作`}
        align="end"
        size="compact"
        rootClassName="library-row-menu library-menu-root"
        triggerClassName="icon-button library-row-menu-trigger"
        triggerAriaLabel={`${title}操作菜单`}
        popoverClassName="library-popover library-row-popover"
        trigger={<MoreIcon />}
      >
        {(close) => (
          <>
            <AppLink
              role="menuitem"
              to={`/tasks/${task.id}${summaryFailed ? "?tab=notes" : ""}`}
              onClick={close}
            >
              <ExternalIcon />
              {task.status === "failed" ? "继续处理" : "查看详情"}
            </AppLink>
            <button
              type="button"
              role="menuitem"
              disabled={!exportReady}
              onClick={() => {
                close();
                onExport(task);
              }}
            >
              <DownloadIcon />导出
            </button>
            {isTerminalStatus(task.status) && (
              <button
                type="button"
                role="menuitem"
                className="is-danger"
                onClick={() => {
                  close();
                  onDelete(task);
                }}
              >
                <TrashIcon />{deleteLabel}
              </button>
            )}
          </>
        )}
      </DropdownMenu>
    </article>
  );
}

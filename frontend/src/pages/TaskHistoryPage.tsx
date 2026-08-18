import { type CSSProperties, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api, isTerminalStatus, retryPollDelay } from "../api/client";
import type { StageRun, Task } from "../api/types";
import { formatDate, statusLabel } from "../app/format";
import { DownloadIcon } from "../app/icons";
import { AppLink } from "../app/router";
import { EmptyState } from "../components/EmptyState";
import { InlineNotice } from "../components/InlineNotice";
import { OutputExportDialog } from "../components/OutputExportDialog";

function taskTitle(task: Task): string {
  const item = task.items[0];
  return item?.title ?? item?.source_display_name ?? "未命名记录";
}

const stageOrder: StageRun["stage"][] = ["source", "transcribe", "translate", "notes"];
const finishedStageStatuses = new Set(["completed", "skipped"]);

const libraryStageLabels: Record<StageRun["stage"], string> = {
  source: "获取来源",
  transcribe: "生成字幕",
  translate: "翻译字幕",
  notes: "生成笔记",
};

interface ProgressView {
  label: string;
  ratio: number;
  determinate: boolean;
  valueText: string;
}

function latestStageRuns(runs: StageRun[]): StageRun[] {
  const latest = new Map<StageRun["stage"], StageRun>();
  for (const run of runs) {
    const current = latest.get(run.stage);
    if (!current || run.attempt > current.attempt) latest.set(run.stage, run);
  }
  return stageOrder.flatMap((stage) => {
    const run = latest.get(stage);
    return run ? [run] : [];
  });
}

function taskProgress(task: Task): ProgressView | null {
  if (isTerminalStatus(task.status)) return null;
  const runs = latestStageRuns(task.items[0]?.stage_runs ?? []);
  if (runs.length === 0) {
    return {
      label: task.status === "cancel_requested" ? "正在停止" : "等待处理",
      ratio: 0,
      determinate: false,
      valueText: statusLabel(task.status),
    };
  }

  const completed = runs.filter((run) => finishedStageStatuses.has(run.status)).length;
  const current =
    runs.find((run) =>
      ["running", "waiting_external", "cancel_requested"].includes(run.status),
    ) ?? runs.find((run) => !finishedStageStatuses.has(run.status));
  const total = runs.length;
  let ratio = completed / total;
  let determinate = false;

  if (
    current?.progress &&
    typeof current.progress.total === "number" &&
    current.progress.total > 0
  ) {
    const stageRatio = Math.min(
      1,
      Math.max(0, current.progress.current / current.progress.total),
    );
    ratio = Math.min(1, (completed + stageRatio) / total);
    determinate = true;
  }

  const baseLabel = current ? libraryStageLabels[current.stage] : "整理结果";
  const label =
    task.status === "cancel_requested"
      ? "正在停止"
      : current?.status === "waiting_external"
        ? `${baseLabel} · 等待云端`
        : baseLabel;
  return {
    label,
    ratio,
    determinate,
    valueText: determinate
      ? `${label}，${Math.round(ratio * 100)}%`
      : `${label}，已完成 ${completed} / ${total} 个阶段`,
  };
}

function mergeNewestTasks(current: Task[], incoming: Task[]): Task[] {
  const incomingIds = new Set(incoming.map((task) => task.id));
  return [...incoming, ...current.filter((task) => !incomingIds.has(task.id))];
}

function terminalLabel(status: string): string {
  if (status === "completed") return "完成";
  if (status === "completed_with_warnings") return "完成，有提醒";
  return statusLabel(status);
}

function hasExportableOutcome(task: Task): boolean {
  const item = task.items[0];
  if (!item || !isTerminalStatus(task.status)) return false;
  const successfulStages = new Set(
    latestStageRuns(item.stage_runs)
      .filter((run) => finishedStageStatuses.has(run.status))
      .map((run) => run.stage),
  );
  return (
    (task.pipeline_snapshot.audio_export_enabled === true &&
      successfulStages.has("source")) ||
    successfulStages.has("transcribe") ||
    successfulStages.has("notes")
  );
}

export function TaskHistoryPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Task | null>(null);

  const load = useCallback(async (nextCursor: string | null, append: boolean) => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: "30" });
    if (nextCursor) query.set("cursor", nextCursor);
    try {
      const page = await api.requestPage<Task[]>(`/api/tasks?${query.toString()}`);
      setTasks((current) => (append ? [...current, ...page.data] : page.data));
      setCursor(page.nextCursor);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法读取内容库。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(null, false);
  }, [load]);

  const hasActiveTasks = useMemo(
    () => tasks.some((task) => !isTerminalStatus(task.status)),
    [tasks],
  );

  useEffect(() => {
    if (!hasActiveTasks) return;
    let disposed = false;
    let failureCount = 0;
    let timer: number | null = null;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      timer = window.setTimeout(() => void refresh(), delay);
    };
    const refresh = async () => {
      controller = new AbortController();
      try {
        const page = await api.requestPage<Task[]>(
          "/api/tasks?limit=30",
          controller.signal,
        );
        if (disposed) return;
        failureCount = 0;
        setTasks((current) => mergeNewestTasks(current, page.data));
        schedule(document.hidden ? 10_000 : 1_500);
      } catch (caught) {
        if (disposed || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        failureCount += 1;
        schedule(retryPollDelay(failureCount));
      }
    };

    schedule(document.hidden ? 5_000 : 900);
    return () => {
      disposed = true;
      controller?.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [hasActiveTasks]);

  return (
    <div className="page library-page">
      <header className="page-header library-header">
        <div>
          <h1>内容库</h1>
        </div>
      </header>

      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {!loading && !error && tasks.length === 0 && (
        <EmptyState
          title="还没有处理记录"
          description="从一个 B 站链接或本地视频开始。"
          actionLabel="新建处理"
        />
      )}
      {tasks.length > 0 && (
        <div className="library-list">
          {tasks.map((task) => {
            const progress = taskProgress(task);
            const exportReady = hasExportableOutcome(task);
            return (
              <article className="library-row" key={task.id}>
                <div className="library-record">
                  <h2>{taskTitle(task)}</h2>
                  <p>{formatDate(task.created_at)}</p>
                </div>
                <div className="library-status" aria-live="polite">
                  {progress ? (
                    <div className="library-progress">
                      <span className="library-progress-label">{progress.label}</span>
                      <div
                        className={`library-progress-track${
                          progress.determinate ? "" : " is-indeterminate"
                        }`}
                        role="progressbar"
                        aria-label={`${taskTitle(task)}处理进度`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={
                          progress.determinate ? Math.round(progress.ratio * 100) : undefined
                        }
                        aria-valuetext={progress.valueText}
                      >
                        <span
                          className="library-progress-fill"
                          style={
                            {
                              "--progress-ratio": progress.ratio,
                            } as CSSProperties
                          }
                        />
                        {!progress.determinate && (
                          <span className="library-progress-activity" aria-hidden="true" />
                        )}
                      </div>
                    </div>
                  ) : (
                    <span className={`library-status-text is-${task.status}`}>
                      {terminalLabel(task.status)}
                    </span>
                  )}
                </div>
                <div className="library-actions">
                  <AppLink className="button button-quiet" to={`/tasks/${task.id}`}>
                    {task.status === "failed" ? "继续处理" : "查看详情"}
                  </AppLink>
                  <button
                    type="button"
                    className="button export-row-button"
                    disabled={!exportReady}
                    onClick={() => setSelected(task)}
                  >
                    <DownloadIcon />
                    导出
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
      {loading && tasks.length === 0 && <p className="muted">正在读取记录…</p>}
      {cursor && (
        <div className="load-more">
          <button className="button" type="button" onClick={() => void load(cursor, true)}>
            加载更早记录
          </button>
        </div>
      )}
      {selected?.items[0] && (
        <OutputExportDialog
          itemId={selected.items[0].id}
          title={taskTitle(selected)}
          open
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

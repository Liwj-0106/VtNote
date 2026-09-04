import {
  createContext,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api, isTerminalStatus, retryPollDelay } from "../../api/client";
import type { Task } from "../../api/types";
import {
  CheckIcon,
  ChevronDownIcon,
  ClipboardIcon,
  CloseIcon,
  PlusIcon,
  PlayIcon,
  SpinnerIcon,
  TasksIcon,
} from "../../app/icons";
import { AppLink, useRouter } from "../../app/router";
import { InlineNotice } from "../../components/InlineNotice";
import { resolveEmbeddedSource } from "../task-detail/SourceVideoPanel";
import {
  hasFailedSummary,
  latestStageRuns,
  mergeNewestTasks,
  taskFailureLabel,
  taskProgress,
  taskTitle,
} from "../task-library/model";

const QUEUE_IDS_KEY = "vtnote.taskQueue.ids";
const QUEUE_COLLAPSED_KEY = "vtnote.taskQueue.collapsed";
const QUEUE_POSITION_KEY = "vtnote.taskQueue.position";
const MAX_TRACKED_TASKS = 8;
const MAX_VISIBLE_TASKS = 5;

type ToastTone = "success" | "danger" | "muted";

interface ToastAction {
  label: string;
  to: string;
}

interface ToastOptions {
  action?: ToastAction;
  detail?: string;
  loading?: boolean;
}

interface ToastRecord {
  id: number;
  message: string;
  tone: ToastTone;
  options?: ToastOptions;
}

interface TaskQueueValue {
  registerTasks: (tasks: Task[]) => void;
  notify: (message: string, tone?: ToastTone, options?: ToastOptions) => void;
  pendingPaste: string | null;
  consumePendingPaste: () => void;
}

const emptyQueueValue: TaskQueueValue = {
  registerTasks: () => undefined,
  notify: () => undefined,
  pendingPaste: null,
  consumePendingPaste: () => undefined,
};

const TaskQueueContext = createContext<TaskQueueValue>(emptyQueueValue);

function readTrackedIds(): string[] {
  try {
    const stored = JSON.parse(localStorage.getItem(QUEUE_IDS_KEY) ?? "[]") as unknown;
    if (!Array.isArray(stored)) return [];
    return stored.filter((value): value is string => typeof value === "string").slice(0, MAX_TRACKED_TASKS);
  } catch {
    return [];
  }
}

function readCollapsed(): boolean {
  return localStorage.getItem(QUEUE_COLLAPSED_KEY) === "true";
}

function readQueuePosition(): { x: number; y: number } | null {
  try {
    const stored = JSON.parse(
      localStorage.getItem(QUEUE_POSITION_KEY) ?? "null",
    ) as unknown;
    if (
      stored !== null &&
      typeof stored === "object" &&
      "x" in stored &&
      "y" in stored &&
      typeof stored.x === "number" &&
      typeof stored.y === "number"
    ) {
      return { x: Math.max(8, stored.x), y: Math.max(8, stored.y) };
    }
  } catch {
    // Ignore an invalid local preference and use the default top-right position.
  }
  return null;
}

function completedStage(task: Task, stage: "transcribe" | "notes"): boolean {
  return task.items.some((item) =>
    item.stage_runs.some(
      (run) => run.stage === stage && run.status === "completed",
    ),
  );
}

function terminalMessage(task: Task): {
  message: string;
  tone: ToastTone;
  options?: ToastOptions;
} | null {
  if (hasFailedSummary(task)) {
    return {
      message: taskFailureLabel(task) ?? "总结失败",
      tone: "danger",
      options: {
        action: {
          label: "查看详情",
          to: `/tasks/${task.id}?tab=notes`,
        },
      },
    };
  }
  if (["completed", "completed_with_warnings"].includes(task.status)) {
    const title = taskTitle(task);
    const hasNotes = completedStage(task, "notes");
    const hasTranscript = completedStage(task, "transcribe");
    const destination = hasNotes
      ? `/tasks/${task.id}?tab=notes`
      : hasTranscript
        ? `/tasks/${task.id}?tab=transcript`
        : `/tasks/${task.id}`;
    return {
      message: hasNotes ? `“${title}”总结完成` : `“${title}”处理完成`,
      tone: "success",
      options: {
        detail: hasNotes ? "总结已生成" : "内容已处理完成",
        action: {
          label: hasNotes ? "查看总结" : hasTranscript ? "查看字幕" : "查看结果",
          to: destination,
        },
      },
    };
  }
  if (["failed", "canceled"].includes(task.status)) {
    return { message: taskFailureLabel(task) ?? "失败", tone: "danger" };
  }
  return null;
}

const stageLoadingLabels = {
  source: "获取视频…",
  transcribe: "生成字幕…",
  translate: "翻译字幕…",
  notes: "生成总结…",
} as const;

function activeStageRun(task: Task) {
  const runs = latestStageRuns(task.items[0]?.stage_runs ?? []);
  return (
    runs.find((run) =>
      ["running", "waiting_external", "cancel_requested"].includes(run.status),
    ) ?? runs.find((run) => !["completed", "skipped"].includes(run.status))
  );
}

function queueStatus(task: Task): {
  label: string;
  loadingLabel: string | null;
  progress: number | null;
  startedAt: string | null;
  tone: "active" | "success" | "danger" | "muted";
} {
  if (hasFailedSummary(task)) {
    return {
      label: taskFailureLabel(task) ?? "总结失败",
      loadingLabel: null,
      progress: null,
      startedAt: null,
      tone: "danger",
    };
  }
  if (["completed", "completed_with_warnings"].includes(task.status)) {
    return {
      label: "完成",
      loadingLabel: null,
      progress: 1,
      startedAt: null,
      tone: "success",
    };
  }
  if (["failed", "canceled"].includes(task.status)) {
    return {
      label: taskFailureLabel(task) ?? "失败",
      loadingLabel: null,
      progress: null,
      startedAt: null,
      tone: "danger",
    };
  }
  const stageRun = activeStageRun(task);
  const stageProgress = stageRun?.progress;
  const fallbackProgress = taskProgress(task);
  const progress =
    stageProgress &&
    typeof stageProgress.total === "number" &&
    stageProgress.total > 0
      ? Math.min(1, Math.max(0, stageProgress.current / stageProgress.total))
      : fallbackProgress?.determinate
        ? fallbackProgress.ratio
        : null;
  return {
    label: "处理中",
    loadingLabel:
      task.status === "cancel_requested"
        ? "正在停止…"
        : stageRun
          ? stageLoadingLabels[stageRun.stage]
          : "等待处理…",
    progress,
    startedAt: stageRun?.started_at ?? task.created_at,
    tone: "active",
  };
}

function formatElapsed(startedAt: string | null, now: number): string | null {
  if (!startedAt) return null;
  const started = Date.parse(startedAt);
  if (!Number.isFinite(started)) return null;
  const totalSeconds = Math.max(0, Math.floor((now - started) / 1_000));
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours}小时${minutes % 60}分`;
}

function QueueTaskCard({
  task,
  onRemove,
}: {
  task: Task;
  onRemove: (taskId: string) => void;
}) {
  const [now, setNow] = useState(Date.now);
  const item = task.items[0];
  const status = queueStatus(task);
  const source = item
    ? resolveEmbeddedSource(item.source_kind, item.source_locator)
    : null;
  const youtubeId =
    source?.provider === "YouTube"
      ? new URL(source.originalUrl).searchParams.get("v")
      : null;
  const thumbnail = youtubeId
    ? `https://i.ytimg.com/vi/${encodeURIComponent(youtubeId)}/mqdefault.jpg`
    : null;
  const transcriptReady = item?.stage_runs.some(
    (run) => run.stage === "transcribe" && run.status === "completed",
  );
  const notesReady = latestStageRuns(item?.stage_runs ?? []).some(
    (run) => run.stage === "notes" && run.status === "completed",
  );
  const summaryFailed = hasFailedSummary(task);
  const title = taskTitle(task);
  const elapsed = formatElapsed(status.startedAt, now);
  const percent =
    status.progress === null ? null : Math.round(status.progress * 100);
  const statusIcon =
    status.tone === "success" ? (
      <CheckIcon />
    ) : status.tone === "danger" ? (
      <CloseIcon />
    ) : (
      <SpinnerIcon />
    );

  useEffect(() => {
    if (status.tone !== "active") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [status.tone]);

  return (
    <li className={`task-queue-item is-${status.tone}`}>
      <div className="task-queue-card-main">
        <div className="task-queue-preview" aria-hidden="true">
          <span className="task-queue-preview-fallback">
            <PlayIcon />
            <small>{source?.provider ?? "本地"}</small>
          </span>
          {thumbnail && (
            <img
              src={thumbnail}
              alt=""
              referrerPolicy="no-referrer"
              onError={(event) => event.currentTarget.remove()}
            />
          )}
        </div>
        <div className="task-queue-card-copy">
          <span className="task-queue-item-status">
            <span className="task-queue-status-icon" aria-hidden="true">
              {statusIcon}
            </span>
            {status.label}
          </span>
          {source ? (
            <a
              className="task-queue-source-title"
              href={source.originalUrl}
              target="_blank"
              rel="noreferrer"
              title={title}
            >
              {title}
            </a>
          ) : (
            <span className="task-queue-source-title" title={title}>
              {title}
            </span>
          )}
        </div>
        <button
          className="task-queue-remove"
          type="button"
          aria-label={`从队列移除${title}`}
          onClick={() => onRemove(task.id)}
        >
          <CloseIcon />
        </button>
      </div>
      {status.tone === "active" && (
        <div className="task-queue-progress" aria-label={status.loadingLabel ?? "处理中"}>
          <span className="task-queue-progress-track" aria-hidden="true">
            <span
              className={status.progress === null ? "is-indeterminate" : ""}
              style={
                status.progress === null
                  ? undefined
                  : {
                      "--queue-progress": String(status.progress),
                    } as CSSProperties
              }
            />
          </span>
          <span className="task-queue-progress-copy">
            <strong>{status.loadingLabel}</strong>
            <small>
              {[elapsed, percent === null ? null : `${percent}%`]
                .filter(Boolean)
                .join(" · ")}
            </small>
          </span>
        </div>
      )}
      <div className="task-queue-card-actions">
        {notesReady ? (
          <AppLink to={`/tasks/${task.id}?tab=notes`} className="task-queue-action is-primary">
            <CheckIcon />
            查看总结
          </AppLink>
        ) : summaryFailed ? (
          <AppLink to={`/tasks/${task.id}?tab=notes`} className="task-queue-action is-primary">
            <CloseIcon />
            查看详情
          </AppLink>
        ) : (
          <span className="task-queue-action is-disabled" aria-disabled="true">
            <CheckIcon />
            查看总结
          </span>
        )}
        {transcriptReady ? (
          <AppLink to={`/tasks/${task.id}?tab=transcript`} className="task-queue-action">
            <ClipboardIcon />
            查看字幕
          </AppLink>
        ) : (
          <span className="task-queue-action is-disabled" aria-disabled="true">
            <ClipboardIcon />
            查看字幕
          </span>
        )}
      </div>
    </li>
  );
}

function TaskQueueDock({
  tasks,
  collapsed,
  onToggle,
  onRemove,
  onQuickPaste,
  onClose,
}: {
  tasks: Task[];
  collapsed: boolean;
  onToggle: () => void;
  onRemove: (taskId: string) => void;
  onQuickPaste: () => Promise<void>;
  onClose: () => void;
}) {
  const dock = useRef<HTMLElement | null>(null);
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    left: number;
    top: number;
    moved: boolean;
  } | null>(null);
  const suppressToggle = useRef(false);
  const [position, setPosition] = useState(readQueuePosition);
  const [draggingDock, setDraggingDock] = useState(false);
  const [pasting, setPasting] = useState(false);

  useEffect(() => {
    const clampToViewport = () => {
      setPosition((current) => {
        const bounds = dock.current?.getBoundingClientRect();
        if (!current || !bounds) return current;
        const next = {
          x: Math.min(
            Math.max(8, window.innerWidth - bounds.width - 8),
            Math.max(8, current.x),
          ),
          y: Math.min(
            Math.max(8, window.innerHeight - bounds.height - 8),
            Math.max(8, current.y),
          ),
        };
        if (next.x === current.x && next.y === current.y) return current;
        localStorage.setItem(QUEUE_POSITION_KEY, JSON.stringify(next));
        return next;
      });
    };
    const frame = window.requestAnimationFrame(clampToViewport);
    window.addEventListener("resize", clampToViewport);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", clampToViewport);
    };
  }, [collapsed, tasks.length]);

  if (tasks.length === 0) return null;

  const queueTones = tasks.map((task) => queueStatus(task).tone);
  const activeCount = queueTones.filter((tone) => tone === "active").length;
  const completedCount = queueTones.filter((tone) => tone === "success").length;
  const failedCount = queueTones.filter((tone) => tone === "danger").length;
  const summaries = [
    activeCount > 0 ? { label: "处理中", count: activeCount, tone: "active" } : null,
    completedCount > 0 ? { label: "完成", count: completedCount, tone: "success" } : null,
    failedCount > 0 ? { label: "失败", count: failedCount, tone: "danger" } : null,
  ].filter((value): value is { label: string; count: number; tone: string } => Boolean(value));
  const summary = summaries.map((part) => `${part.count} ${part.label}`).join(" · ");
  const positionStyle = position
    ? ({ left: position.x, top: position.y, right: "auto" } as CSSProperties)
    : undefined;

  const startDockDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 || !dock.current) return;
    const bounds = dock.current.getBoundingClientRect();
    drag.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      left: bounds.left,
      top: bounds.top,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveDock = (event: ReactPointerEvent<HTMLElement>) => {
    if (drag.current?.pointerId !== event.pointerId || !dock.current) return;
    const deltaX = event.clientX - drag.current.startX;
    const deltaY = event.clientY - drag.current.startY;
    if (!drag.current.moved && Math.hypot(deltaX, deltaY) < 5) return;
    drag.current.moved = true;
    setDraggingDock(true);
    const bounds = dock.current.getBoundingClientRect();
    const x = Math.min(
      Math.max(8, window.innerWidth - bounds.width - 8),
      Math.max(8, drag.current.left + deltaX),
    );
    const y = Math.min(
      Math.max(8, window.innerHeight - bounds.height - 8),
      Math.max(8, drag.current.top + deltaY),
    );
    setPosition({ x, y });
  };
  const finishDockDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    suppressToggle.current = drag.current.moved;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag.current = null;
    setDraggingDock(false);
    setPosition((current) => {
      if (current) localStorage.setItem(QUEUE_POSITION_KEY, JSON.stringify(current));
      return current;
    });
  };

  if (collapsed) {
    return (
      <aside
        ref={dock}
        className={`task-queue-dock is-collapsed${draggingDock ? " is-dragging" : ""}`}
        aria-label="处理队列"
        style={positionStyle}
      >
        <button
          className="task-queue-toggle"
          type="button"
          aria-label="展开处理队列"
          aria-expanded="false"
          onPointerDown={(event) => startDockDrag(event)}
          onPointerMove={(event) => moveDock(event)}
          onPointerUp={(event) => finishDockDrag(event)}
          onPointerCancel={(event) => {
            finishDockDrag(event);
            suppressToggle.current = false;
          }}
          onClick={() => {
            if (suppressToggle.current) {
              suppressToggle.current = false;
              return;
            }
            onToggle();
          }}
        >
          <TasksIcon />
          <span className="task-queue-title">处理队列</span>
        </button>
      </aside>
    );
  }

  return (
    <aside
      ref={dock}
      className={`task-queue-dock${draggingDock ? " is-dragging" : ""}`}
      aria-label="处理队列"
      style={positionStyle}
    >
      <div
        className="task-queue-header"
        onPointerDown={(event) => startDockDrag(event)}
        onPointerMove={(event) => moveDock(event)}
        onPointerUp={(event) => finishDockDrag(event)}
        onPointerCancel={(event) => finishDockDrag(event)}
      >
        <div className="task-queue-heading">
          <TasksIcon />
          <strong>处理队列 ({tasks.length})</strong>
        </div>
        <div className="task-queue-header-actions">
          <button
            className="task-queue-collapse"
            type="button"
            aria-label="收起处理队列"
            aria-expanded="true"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={onToggle}
          >
            <ChevronDownIcon />
          </button>
          <button
            className="task-queue-close"
            type="button"
            aria-label="关闭处理队列"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>
      </div>
      <div className="task-queue-status-summary" aria-label={summary}>
        {summaries.map((part) => (
          <span key={part.label} className={`is-${part.tone}`}>
            <i aria-hidden="true" />
            {part.count} {part.label}
          </span>
        ))}
      </div>
      <ol className="task-queue-list">
        {tasks.slice(0, MAX_VISIBLE_TASKS).map((task) => (
          <QueueTaskCard key={task.id} task={task} onRemove={onRemove} />
        ))}
      </ol>
      <div className="task-queue-footer">
        <AppLink to="/" className="task-queue-add">
          <PlusIcon />
          继续添加新内容
        </AppLink>
        <button
          className="task-queue-quick-paste"
          type="button"
          aria-label="快速粘贴链接"
          title="快速粘贴链接"
          disabled={pasting}
          onClick={() => {
            setPasting(true);
            void onQuickPaste().finally(() => setPasting(false));
          }}
        >
          {pasting ? <SpinnerIcon /> : <ClipboardIcon />}
        </button>
      </div>
    </aside>
  );
}

export function TaskQueueProvider({ children }: { children: ReactNode }) {
  const { navigate } = useRouter();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [trackedIds, setTrackedIds] = useState<string[]>(readTrackedIds);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [queueVisible, setQueueVisible] = useState(true);
  const [pendingPaste, setPendingPaste] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const trackedIdsRef = useRef(trackedIds);
  const toastId = useRef(0);
  const knownStatuses = useRef(new Map<string, string>());
  const initialized = useRef(false);

  const updateTrackedIds = useCallback((nextIds: string[]) => {
    const unique = [...new Set(nextIds)].slice(0, MAX_TRACKED_TASKS);
    trackedIdsRef.current = unique;
    setTrackedIds(unique);
    localStorage.setItem(QUEUE_IDS_KEY, JSON.stringify(unique));
  }, []);

  const notify = useCallback<TaskQueueValue["notify"]>(
    (message, tone = "success", options) => {
      const id = ++toastId.current;
      setToasts((current) => [
        ...current.slice(-3),
        { id, message, tone, options },
      ]);
    },
    [],
  );

  const consumePendingPaste = useCallback(() => setPendingPaste(null), []);

  const quickPaste = useCallback(async () => {
    try {
      const clipboardText = await navigator.clipboard.readText();
      if (!clipboardText.trim()) {
        notify("剪贴板为空", "muted");
        return;
      }
      setPendingPaste(clipboardText);
      navigate("/");
      notify("已粘贴链接");
    } catch {
      notify("无法读取剪贴板", "danger");
    }
  }, [navigate, notify]);

  const registerTasks = useCallback(
    (createdTasks: Task[]) => {
      const validTasks = createdTasks.filter((task) => Boolean(task?.id));
      if (validTasks.length === 0) return;
      for (const task of validTasks) knownStatuses.current.set(task.id, task.status);
      updateTrackedIds([
        ...validTasks.map((task) => task.id),
        ...trackedIdsRef.current,
      ]);
      setTasks((current) => mergeNewestTasks(current, validTasks));
      setQueueVisible(true);
      notify(
        validTasks.length === 1
          ? "正在处理"
          : `${validTasks.length} 个任务正在处理`,
        "muted",
        { loading: true },
      );
    },
    [notify, updateTrackedIds],
  );

  const refresh = useCallback(async () => {
    const page = await api.requestPage<Task[]>("/api/tasks?limit=30");
    const activeIds = page.data
      .filter((task) => !isTerminalStatus(task.status))
      .map((task) => task.id);
    const nextTrackedIds = [...activeIds, ...trackedIdsRef.current];
    updateTrackedIds(nextTrackedIds);
    const tracked = new Set(trackedIdsRef.current);
    const visible = page.data.filter((task) => tracked.has(task.id));

    for (const task of visible) {
      const previousStatus = knownStatuses.current.get(task.id);
      if (
        initialized.current &&
        previousStatus !== undefined &&
        previousStatus !== task.status &&
        isTerminalStatus(task.status)
      ) {
        const result = terminalMessage(task);
        if (result) notify(result.message, result.tone, result.options);
      }
      knownStatuses.current.set(task.id, task.status);
    }
    initialized.current = true;
    setTasks((current) =>
      mergeNewestTasks(current, visible).filter((task) => tracked.has(task.id)),
    );
  }, [notify, updateTrackedIds]);

  useEffect(() => {
    let disposed = false;
    void refresh().catch(() => {
      if (!disposed) initialized.current = true;
    });
    return () => {
      disposed = true;
    };
  }, [refresh]);

  const hasActiveTasks = tasks.some((task) => !isTerminalStatus(task.status));

  useEffect(() => {
    if (!hasActiveTasks) return;
    let disposed = false;
    let failures = 0;
    let timer: number | null = null;

    const poll = async () => {
      try {
        await refresh();
        failures = 0;
        if (!disposed) timer = window.setTimeout(poll, document.hidden ? 10_000 : 1_500);
      } catch {
        failures += 1;
        if (!disposed) timer = window.setTimeout(poll, retryPollDelay(failures));
      }
    };

    timer = window.setTimeout(poll, document.hidden ? 5_000 : 900);
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [hasActiveTasks, refresh]);

  const visibleTasks = useMemo(() => {
    const order = new Map(trackedIds.map((id, index) => [id, index]));
    return tasks
      .filter((task) => order.has(task.id))
      .sort((left, right) => {
        const activityDifference = Number(isTerminalStatus(left.status)) - Number(isTerminalStatus(right.status));
        if (activityDifference !== 0) return activityDifference;
        return (order.get(left.id) ?? 0) - (order.get(right.id) ?? 0);
      });
  }, [tasks, trackedIds]);

  const value = useMemo(
    () => ({ registerTasks, notify, pendingPaste, consumePendingPaste }),
    [consumePendingPaste, notify, pendingPaste, registerTasks],
  );

  return (
    <TaskQueueContext.Provider value={value}>
      {children}
      {toasts.map((toast) => (
        <InlineNotice
          key={toast.id}
          tone={toast.tone === "muted" ? "info" : toast.tone}
          title={toast.options?.detail ? toast.message : undefined}
          loading={toast.options?.loading}
          onDismiss={() => {
            setToasts((current) => current.filter((currentToast) => currentToast.id !== toast.id));
          }}
        >
          {toast.options?.detail ?? toast.message}
          {toast.options?.action ? (
            <AppLink className="toast-action" to={toast.options.action.to}>
              {toast.options.action.label}
            </AppLink>
          ) : null}
        </InlineNotice>
      ))}
      {queueVisible && (
        <TaskQueueDock
          tasks={visibleTasks}
          collapsed={collapsed}
          onToggle={() => {
            setCollapsed((current) => {
              const next = !current;
              localStorage.setItem(QUEUE_COLLAPSED_KEY, String(next));
              return next;
            });
          }}
          onClose={() => setQueueVisible(false)}
          onRemove={(taskId) => {
            updateTrackedIds(
              trackedIdsRef.current.filter((trackedId) => trackedId !== taskId),
            );
          }}
          onQuickPaste={quickPaste}
        />
      )}
    </TaskQueueContext.Provider>
  );
}

export function useTaskQueue(): TaskQueueValue {
  return useContext(TaskQueueContext);
}

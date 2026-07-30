import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Task } from "../api/types";
import {
  formatDate,
  sourceLabel,
  stageLabel,
  statusLabel,
} from "../app/format";
import { AppLink, useRouter } from "../app/router";
import { EmptyState } from "../components/EmptyState";
import { InlineNotice } from "../components/InlineNotice";
import { StatusBadge } from "../components/StatusBadge";

function activeStage(task: Task): string | null {
  const runs = task.items[0]?.stage_runs ?? [];
  const active = runs.find((run) =>
    ["queued", "running", "waiting_external", "cancel_requested"].includes(
      run.status,
    ),
  );
  return active ? stageLabel(active.stage) : null;
}

function taskTitle(task: Task): string {
  const item = task.items[0];
  return (
    item?.title ??
    item?.source_display_name ??
    (item?.source_kind === "bilibili" ? "Bilibili 视频" : null) ??
    (item?.source_kind === "youtube" ? "YouTube 视频" : null) ??
    "未命名任务"
  );
}

export function TaskHistoryPage() {
  const { path, navigate } = useRouter();
  const initialStatus =
    new URLSearchParams(path.split("?")[1] ?? "").get("status") ?? "";
  const [status, setStatus] = useState(initialStatus);
  const [search, setSearch] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (nextCursor: string | null, append: boolean) => {
      setLoading(true);
      setError(null);
      const query = new URLSearchParams({ limit: "30" });
      if (status) query.set("status", status);
      if (nextCursor) query.set("cursor", nextCursor);
      try {
        const page = await api.requestPage<Task[]>(
          `/api/tasks?${query.toString()}`,
        );
        setTasks((current) =>
          append ? [...current, ...page.data] : page.data,
        );
        setCursor(page.nextCursor);
      } catch (caught) {
        setError(
          caught instanceof ApiError
            ? caught.message
            : "无法读取任务，请稍后重试。",
        );
      } finally {
        setLoading(false);
      }
    },
    [status],
  );

  useEffect(() => {
    void load(null, false);
  }, [load]);

  const visible = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    if (!keyword) return tasks;
    return tasks.filter((task) => {
      const item = task.items[0];
      return [taskTitle(task), item?.source_display_name, item?.source_kind]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword));
    });
  }, [search, tasks]);

  return (
    <div className="page history-page">
      <header className="page-header">
        <div>
          <p className="page-kicker">Library</p>
          <h1>任务</h1>
          <p className="page-intro">继续查看处理进度，或打开已经生成的文字。</p>
        </div>
        <AppLink className="button button-primary" to="/">
          新建任务
        </AppLink>
      </header>

      <div className="history-tools">
        <label className="field search-field">
          <span className="visually-hidden">搜索任务</span>
          <input
            className="text-input"
            type="search"
            value={search}
            placeholder="搜索标题或来源"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label className="field filter-field">
          <span className="visually-hidden">状态筛选</span>
          <select
            className="select-input"
            value={status}
            onChange={(event) => {
              const next = event.target.value;
              setStatus(next);
              navigate(next ? `/tasks?status=${encodeURIComponent(next)}` : "/tasks", {
                replace: true,
              });
            }}
          >
            <option value="">全部状态</option>
            <option value="running">处理中</option>
            <option value="queued">等待处理</option>
            <option value="completed">已完成</option>
            <option value="completed_with_warnings">有提醒</option>
            <option value="failed">需要处理</option>
            <option value="canceled">已停止</option>
          </select>
        </label>
      </div>

      {error && (
        <InlineNotice tone="danger" title="任务列表暂时不可用">
          {error}
        </InlineNotice>
      )}
      {!loading && !error && visible.length === 0 && (
        <EmptyState
          title={tasks.length === 0 ? "还没有任务" : "没有符合条件的任务"}
          description={
            tasks.length === 0
              ? "添加一个公开链接或本地文件，生成第一份字幕。"
              : "换一个关键词或清除状态筛选。"
          }
          actionLabel={tasks.length === 0 ? "新建任务" : undefined}
        />
      )}
      {visible.length > 0 && (
        <div className="task-list">
          {visible.map((task) => {
            const item = task.items[0];
            const stage = activeStage(task);
            return (
              <article key={task.id} className="task-list-item">
                <AppLink
                  to={`/tasks/${task.id}`}
                  className="task-row-link"
                  aria-label={`查看 ${taskTitle(task)}`}
                >
                  <div className="task-primary">
                    <h2>{taskTitle(task)}</h2>
                    <p>
                      {sourceLabel(item?.source_kind ?? "unknown")} ·{" "}
                      {formatDate(task.created_at)}
                    </p>
                  </div>
                  <div className="task-state">
                    <StatusBadge status={task.status} />
                    {stage && !["completed", "failed", "canceled"].includes(task.status) && (
                      <small>当前：{stage}</small>
                    )}
                    {!stage && <small>{statusLabel(task.status)}</small>}
                  </div>
                </AppLink>
              </article>
            );
          })}
        </div>
      )}
      {loading && tasks.length === 0 && (
        <p className="muted" role="status">
          正在读取任务…
        </p>
      )}
      {cursor && (
        <div className="load-more">
          <button
            type="button"
            className="button"
            disabled={loading}
            onClick={() => void load(cursor, true)}
          >
            {loading ? "正在读取…" : "加载更早任务"}
          </button>
        </div>
      )}
    </div>
  );
}

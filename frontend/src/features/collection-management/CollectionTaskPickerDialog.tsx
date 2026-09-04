import { useEffect, useMemo, useState } from "react";
import { ApiError, api, isTerminalStatus } from "../../api/client";
import type { Task } from "../../api/types";
import { FormDialog } from "../../components/FormDialog";
import { InlineNotice } from "../../components/InlineNotice";
import { SearchField } from "../../components/SearchField";
import { Skeleton, SkeletonStatus } from "../../components/Skeleton";
import { taskTitle } from "../task-library/model";

export function CollectionTaskPickerDialog({
  open,
  collectionId,
  currentTaskIds,
  onClose,
  onAdded,
}: {
  open: boolean;
  collectionId: string;
  currentTaskIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setQuery("");
    setSelectedTaskIds(new Set());
    api.requestPage<Task[]>("/api/tasks?limit=100", controller.signal)
      .then((result) => {
        setTasks(result.data.filter(
          (task) => isTerminalStatus(task.status) && !currentTaskIds.has(task.id),
        ));
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof ApiError ? caught.message : "无法读取总结记录。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [currentTaskIds, open]);

  const visibleTasks = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    if (!normalized) return tasks;
    return tasks.filter((task) => taskTitle(task).toLocaleLowerCase("zh-CN").includes(normalized));
  }, [query, tasks]);

  const addSelected = async () => {
    if (selectedTaskIds.size === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.request("/api/library/organize", {
        method: "POST",
        body: {
          task_ids: [...selectedTaskIds],
          collection_ids: [collectionId],
          tag_ids: [],
          operation: "add",
        },
      });
      onAdded();
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法添加内容。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <FormDialog
      open={open}
      title="添加内容"
      className="collection-task-picker-dialog"
      busy={busy}
      onClose={onClose}
    >
      <div className="collection-task-picker">
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        <SearchField
          className="collection-task-search"
          label="搜索可添加的总结记录"
          clearLabel="清除总结搜索"
          value={query}
          placeholder="搜索总结记录"
          onChange={(event) => setQuery(event.currentTarget.value)}
          onClear={() => setQuery("")}
        />

        {loading ? (
          <SkeletonStatus className="collection-task-picker-list" label="正在读取总结记录">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton className="collection-task-picker-skeleton" key={index} />
            ))}
          </SkeletonStatus>
        ) : visibleTasks.length > 0 ? (
          <div className="collection-task-picker-list">
            {visibleTasks.map((task) => {
              const selected = selectedTaskIds.has(task.id);
              return (
                <label className="collection-task-option" key={task.id}>
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => {
                      const next = new Set(selectedTaskIds);
                      if (selected) next.delete(task.id);
                      else next.add(task.id);
                      setSelectedTaskIds(next);
                    }}
                  />
                  <span>{taskTitle(task)}</span>
                </label>
              );
            })}
          </div>
        ) : (
          <div className="collection-task-picker-empty">没有可添加的总结记录</div>
        )}

        <div className="actions dialog-actions">
          <button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || selectedTaskIds.size === 0}
            onClick={() => void addSelected()}
          >
            {busy ? "添加中…" : `添加${selectedTaskIds.size > 0 ? `（${selectedTaskIds.size}）` : ""}`}
          </button>
        </div>
      </div>
    </FormDialog>
  );
}

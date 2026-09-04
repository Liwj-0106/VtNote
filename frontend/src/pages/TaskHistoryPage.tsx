import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, api, isTerminalStatus, retryPollDelay } from "../api/client";
import type { LibrarySearchResult, Task, TaskItem } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import { OutputExportDialog } from "../components/OutputExportDialog";
import { segmentedTabId } from "../components/SegmentedTabs";
import { useRouter } from "../app/router";
import { OrganizeDialog } from "../features/library-discovery/OrganizeDialog";
import type { LibraryFilters } from "../features/library-discovery/types";
import { useLibraryDiscovery } from "../features/library-discovery/useLibraryDiscovery";
import {
  LibraryToolbar,
  LIBRARY_RESULTS_PANEL_ID,
  LIBRARY_STATUS_TABS_ID,
} from "../features/task-library/LibraryToolbar";
import { NewSummaryDialog } from "../features/task-library/NewSummaryDialog";
import { TaskLibraryWorkspace } from "../features/task-library/TaskLibraryWorkspace";
import { useTaskLibrarySelection } from "../features/task-library/useTaskLibrarySelection";
import { useTaskQueue } from "../features/task-queue/TaskQueueProvider";
import {
  deleteErrorMessage,
  mergeNewestTasks,
  taskRetryPlan,
  taskTitle,
  type TaskRetryPlan,
} from "../features/task-library/model";
import type {
  LibraryProperty,
  LibraryViewMode,
} from "../features/task-library/types";

const defaultProperties = new Set<LibraryProperty>([
  "cover",
  "title",
  "publishedAt",
]);

async function requestAllTaskPages(signal?: AbortSignal): Promise<Task[]> {
  const tasks: Task[] = [];
  const seenCursors = new Set<string>();
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) query.set("cursor", cursor);
    const page = await api.requestPage<Task[]>(
      `/api/tasks?${query.toString()}`,
      signal,
    );
    tasks.push(...page.data);
    if (page.nextCursor === null) break;
    if (seenCursors.has(page.nextCursor)) throw new Error("task cursor repeated");
    seenCursors.add(page.nextCursor);
    cursor = page.nextCursor;
  } while (cursor);
  return tasks;
}

export function TaskHistoryPage() {
  const { path } = useRouter();
  const { registerTasks } = useTaskQueue();
  const initialLibraryFilters = useMemo<Partial<LibraryFilters>>(() => {
    const query = new URLSearchParams(path.split("?")[1] ?? "");
    return {
      collectionId: query.get("collection_id") ?? "",
      unclassified: query.get("unclassified") === "true",
    };
  }, [path]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [searchResults, setSearchResults] = useState<LibrarySearchResult[]>([]);
  const [organizing, setOrganizing] = useState(false);
  const [newSummaryOpen, setNewSummaryOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedExportTask, setSelectedExportTask] = useState<Task | null>(null);
  const [exportDialogOpen, setExportDialogOpen] = useState(false);
  const [deleteTargets, setDeleteTargets] = useState<Task[]>([]);
  const [deleting, setDeleting] = useState(false);
  const [retryTarget, setRetryTarget] = useState<{
    task: Task;
    plan: TaskRetryPlan;
  } | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [removingTaskIds, setRemovingTaskIds] = useState<Set<string>>(new Set());
  const [view, setView] = useState<LibraryViewMode>("table");
  const [properties, setProperties] = useState<Set<LibraryProperty>>(
    () => new Set(defaultProperties),
  );
  const [pageSize, setPageSize] = useState(30);
  const [page, setPage] = useState(0);
  const libraryListRef = useRef<HTMLDivElement>(null);
  const deletedTaskIdsRef = useRef<Set<string>>(new Set());
  const {
    filters,
    setFilters,
    metadata,
    setMetadata,
    metadataError,
    searchActive,
    discoveryQuery,
  } = useLibraryDiscovery(initialLibraryFilters);

  useEffect(() => {
    setFilters((current) => {
      const collectionId = initialLibraryFilters.collectionId ?? "";
      const unclassified = initialLibraryFilters.unclassified ?? false;
      if (
        current.collectionId === collectionId &&
        current.unclassified === unclassified
      ) {
        return current;
      }
      return { ...current, collectionId, unclassified };
    });
  }, [initialLibraryFilters.collectionId, initialLibraryFilters.unclassified, setFilters]);

  const scopeLabel = filters.unclassified
    ? "未分类"
    : metadata.collections.find((collection) => collection.id === filters.collectionId)?.name;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (searchActive) {
      try {
        const results = await api.request<LibrarySearchResult[]>(
          `/api/library/search?${discoveryQuery}`,
        );
        setSearchResults(results);
        setTasks(results.map((result) => result.task));
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.message : "无法搜索总结记录。");
      } finally {
        setLoading(false);
      }
      return;
    }
    try {
      const result = await requestAllTaskPages();
      const visible = result.filter(
        (task) => !deletedTaskIdsRef.current.has(task.id),
      );
      setTasks(visible);
      setSearchResults([]);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法读取总结记录。");
    } finally {
      setLoading(false);
    }
  }, [discoveryQuery, searchActive]);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActiveTasks = useMemo(
    () => tasks.some((task) => !isTerminalStatus(task.status)),
    [tasks],
  );

  useEffect(() => {
    if (!hasActiveTasks || searchActive) return;
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
        const result = await requestAllTaskPages(controller.signal);
        if (disposed) return;
        failureCount = 0;
        setTasks((current) =>
          mergeNewestTasks(
            current,
            result.filter((task) => !deletedTaskIdsRef.current.has(task.id)),
          ),
        );
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
  }, [hasActiveTasks, searchActive]);

  const pageCount = Math.max(1, Math.ceil(tasks.length / pageSize));
  const visibleTasks = useMemo(
    () => tasks.slice(page * pageSize, (page + 1) * pageSize),
    [page, pageSize, tasks],
  );
  const {
    selectedTaskIds,
    setSelectedTaskIds,
    allVisibleSelected,
    selectTask,
    toggleVisible,
  } = useTaskLibrarySelection({
    tasks,
    visibleTasks,
    listRef: libraryListRef,
    suspendKeyboard:
      deleteTargets.length > 0 ||
      selectedExportTask !== null ||
      retryTarget !== null,
  });

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount - 1));
  }, [pageCount]);

  const confirmDelete = async () => {
    if (deleteTargets.length === 0 || deleting) return;
    setDeleting(true);
    setError(null);
    const targets = [...deleteTargets];
    try {
      const results = await Promise.allSettled(
        targets.map((task) =>
          api.request<void>(`/api/tasks/${task.id}`, { method: "DELETE" }),
        ),
      );
      const deletedTaskIds = targets.flatMap((task, index) =>
        results[index]?.status === "fulfilled" ? [task.id] : [],
      );
      const failedResults = results.filter(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );

      for (const taskId of deletedTaskIds) deletedTaskIdsRef.current.add(taskId);
      setRemovingTaskIds((current) => new Set([...current, ...deletedTaskIds]));
      setSelectedTaskIds((current) => {
        const next = new Set(current);
        for (const taskId of deletedTaskIds) next.delete(taskId);
        return next;
      });
      setDeleteTargets([]);
      if (failedResults.length > 0) {
        setError(
          deletedTaskIds.length > 0
            ? `已删除 ${deletedTaskIds.length} 条记录，${failedResults.length} 条删除失败。`
            : deleteErrorMessage(failedResults[0].reason),
        );
      }
    } catch (caught) {
      setError(deleteErrorMessage(caught));
    } finally {
      setDeleting(false);
    }
  };

  const confirmRetry = async () => {
    if (!retryTarget || retrying) return;
    setRetrying(true);
    setError(null);
    try {
      const retriedItem = await api.request<TaskItem>(
        `/api/tasks/${retryTarget.task.id}/retry`,
        { method: "POST", body: retryTarget.plan.body },
      );
      const queuedTask: Task = {
        ...retryTarget.task,
        status: "queued",
        terminal_reason_code: null,
        updated_at: retriedItem.updated_at,
        items: retryTarget.task.items.map((item) =>
          item.id === retriedItem.id ? retriedItem : item,
        ),
      };
      setTasks((current) =>
        current.map((task) => (task.id === queuedTask.id ? queuedTask : task)),
      );
      setSearchResults((current) =>
        current.map((result) =>
          result.task.id === queuedTask.id
            ? { ...result, task: queuedTask }
            : result,
        ),
      );
      registerTasks([queuedTask]);
      setRetryTarget(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "重试失败，请稍后再试。",
      );
    } finally {
      setRetrying(false);
    }
  };

  const finishTaskRemoval = useCallback((taskId: string) => {
    setTasks((current) => current.filter((task) => task.id !== taskId));
    setRemovingTaskIds((current) => {
      const next = new Set(current);
      next.delete(taskId);
      return next;
    });
  }, []);

  const selectedTasks = tasks.filter((task) => selectedTaskIds.has(task.id));
  const selectedVisibleRows = visibleTasks.filter((task) =>
    selectedTaskIds.has(task.id),
  ).length;
  const canNext = page < pageCount - 1;

  const nextPage = () => setPage((current) => Math.min(pageCount - 1, current + 1));

  const updateFilters = (next: LibraryFilters) => {
    setPage(0);
    setFilters(next);
  };

  const toggleProperty = (property: LibraryProperty) => {
    setProperties((current) => {
      const next = new Set(current);
      if (next.has(property)) {
        if (next.size === 1) return current;
        next.delete(property);
      } else {
        next.add(property);
      }
      return next;
    });
  };

  return (
    <div className="page library-page summary-library-page">
      <LibraryToolbar
        filters={filters}
        onFilters={updateFilters}
        view={view}
        onView={setView}
        properties={properties}
        onToggleProperty={toggleProperty}
        selectedTasks={selectedTasks}
        selectedVisibleRows={selectedVisibleRows}
        visibleRows={visibleTasks.length}
        onAddCollection={() => setOrganizing(true)}
        onDeleteSelected={() => setDeleteTargets(selectedTasks)}
        onNew={() => setNewSummaryOpen(true)}
        pageSize={pageSize}
        onPageSize={(value) => {
          setPageSize(value);
          setPage(0);
        }}
        page={page}
        pageCount={pageCount}
        canPrevious={page > 0}
        canNext={canNext}
        onPrevious={() => setPage((current) => Math.max(0, current - 1))}
        onNext={() => void nextPage()}
        scopeLabel={scopeLabel}
      />

      <MotionPresence present={Boolean(error || metadataError)}>
        {error || metadataError ? (
          <InlineNotice tone="danger">{error || metadataError}</InlineNotice>
        ) : null}
      </MotionPresence>

      <div
        id={LIBRARY_RESULTS_PANEL_ID}
        role="tabpanel"
        aria-labelledby={segmentedTabId(LIBRARY_STATUS_TABS_ID, filters.status)}
      >
        <TaskLibraryWorkspace
          tasks={tasks}
          visibleTasks={visibleTasks}
          searchResults={searchResults}
          loading={loading}
          hasError={Boolean(error || metadataError)}
          view={view}
          properties={properties}
          selectedTaskIds={selectedTaskIds}
          allVisibleSelected={allVisibleSelected}
          removingTaskIds={removingTaskIds}
          listRef={libraryListRef}
          onToggleVisible={toggleVisible}
          onSelect={selectTask}
          onExport={(target) => {
            setSelectedExportTask(target);
            setExportDialogOpen(true);
          }}
          onDelete={(target) => setDeleteTargets([target])}
          onRetry={(target) => {
            const plan = taskRetryPlan(target);
            if (plan) setRetryTarget({ task: target, plan });
          }}
          onRemovalFinished={finishTaskRemoval}
        />
      </div>

      {selectedExportTask?.items[0] && (
        <OutputExportDialog
          itemId={selectedExportTask.items[0].id}
          title={taskTitle(selectedExportTask)}
          open={exportDialogOpen}
          onClose={() => setExportDialogOpen(false)}
          onExited={() => setSelectedExportTask(null)}
        />
      )}

      {selectedTasks.length > 0 && (
        <OrganizeDialog
          open={organizing}
          taskIds={selectedTasks.map((task) => task.id)}
          metadata={metadata}
          onMetadata={setMetadata}
          onClose={() => setOrganizing(false)}
          onApplied={() => void load()}
          collectionsOnly
        />
      )}

      <NewSummaryDialog
        open={newSummaryOpen}
        onClose={() => setNewSummaryOpen(false)}
        onCreated={() => void load()}
      />

      <ConfirmDialog
        open={retryTarget !== null}
        title={
          retryTarget ? `重试“${taskTitle(retryTarget.task)}”？` : "重试任务？"
        }
        description={retryTarget?.plan.description ?? "将从失败阶段继续处理。"}
        confirmLabel="确认重试"
        busy={retrying}
        onConfirm={() => void confirmRetry()}
        onClose={() => {
          if (!retrying) setRetryTarget(null);
        }}
      />

      <ConfirmDialog
        open={deleteTargets.length > 0}
        title={
          deleteTargets.length > 1
            ? `删除选中的 ${deleteTargets.length} 条记录？`
            : deleteTargets[0]
              ? `删除“${taskTitle(deleteTargets[0])}”？`
              : "删除记录？"
        }
        description="相关字幕、总结和缓存文件将永久删除。"
        confirmLabel="删除"
        danger
        busy={deleting}
        onConfirm={() => void confirmDelete()}
        onClose={() => {
          if (!deleting) setDeleteTargets([]);
        }}
      />
    </div>
  );
}

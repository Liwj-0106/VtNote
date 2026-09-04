import {
  useMemo,
  type CSSProperties,
  type MouseEvent,
  type RefObject,
} from "react";
import type { LibrarySearchResult, Task } from "../../api/types";
import { EmptyState } from "../../components/EmptyState";
import { MotionPresence } from "../../components/MotionPresence";
import { Skeleton, SkeletonStatus } from "../../components/Skeleton";
import {
  TaskLibraryRow,
  taskLibraryColumnTemplate,
} from "./TaskLibraryRow";
import type { LibraryProperty, LibraryViewMode } from "./types";

type SelectTask = (
  taskId: string,
  options?: {
    shift?: boolean;
    additive?: boolean;
    toggle?: boolean;
    event?: MouseEvent<HTMLElement>;
  },
) => void;

export function TaskLibraryWorkspace({
  tasks,
  visibleTasks,
  searchResults,
  loading,
  hasError,
  view,
  properties,
  selectedTaskIds,
  allVisibleSelected,
  removingTaskIds,
  listRef,
  onToggleVisible,
  onSelect,
  onExport,
  onDelete,
  onRetry,
  onRemovalFinished,
}: {
  tasks: Task[];
  visibleTasks: Task[];
  searchResults: LibrarySearchResult[];
  loading: boolean;
  hasError: boolean;
  view: LibraryViewMode;
  properties: Set<LibraryProperty>;
  selectedTaskIds: Set<string>;
  allVisibleSelected: boolean;
  removingTaskIds: Set<string>;
  listRef: RefObject<HTMLDivElement | null>;
  onToggleVisible: () => void;
  onSelect: SelectTask;
  onExport: (task: Task) => void;
  onDelete: (task: Task) => void;
  onRetry: (task: Task) => void;
  onRemovalFinished: (taskId: string) => void;
}) {
  const resultByTaskId = useMemo(
    () => new Map(searchResults.map((result) => [result.task.id, result])),
    [searchResults],
  );

  if (loading && tasks.length === 0) return <TaskLibrarySkeleton />;

  if (!loading && !hasError && tasks.length === 0) {
    return (
      <EmptyState
        title="还没有总结记录"
        actionLabel="新增总结"
      />
    );
  }

  if (tasks.length === 0) return null;

  return (
    <section className={`library-workspace is-${view}-view`} aria-label="总结记录列表">
      {(view === "table" || view === "list") && (
        <div
          className="library-table-head"
          style={
            {
              "--library-row-columns": taskLibraryColumnTemplate(properties),
            } as CSSProperties
          }
        >
          <input
            type="checkbox"
            className="library-select-checkbox"
            checked={allVisibleSelected}
            aria-label={allVisibleSelected ? "取消选择当前页" : "选择当前页"}
            onChange={onToggleVisible}
          />
          {properties.has("cover") && <span>封面</span>}
          {properties.has("title") && <span>标题</span>}
          {properties.has("publishedAt") && <span>时间</span>}
          <span className="visually-hidden">操作</span>
        </div>
      )}
      <div className="library-list" ref={listRef} tabIndex={-1}>
        {visibleTasks.map((task) => {
          const result = resultByTaskId.get(task.id);
          return (
            <MotionPresence
              key={task.id}
              present={!removingTaskIds.has(task.id)}
              initial={false}
              onExited={() => onRemovalFinished(task.id)}
            >
              <TaskLibraryRow
                task={task}
                selected={selectedTaskIds.has(task.id)}
                view={view}
                properties={properties}
                onSelect={onSelect}
                onExport={onExport}
                onDelete={onDelete}
                onRetry={onRetry}
                collections={result?.collections ?? []}
                tags={result?.tags ?? []}
              />
            </MotionPresence>
          );
        })}
      </div>
    </section>
  );
}

function TaskLibrarySkeleton() {
  return (
    <SkeletonStatus className="library-list library-table-skeleton" label="正在读取总结记录">
      {Array.from({ length: 4 }, (_, index) => (
        <div className="library-row library-row-skeleton" key={index}>
          <Skeleton className="library-skeleton-checkbox is-block" />
          <Skeleton className="library-skeleton-cover is-block" />
          <div className="library-skeleton-record">
            <Skeleton className="library-skeleton-title" />
            <Skeleton className="library-skeleton-date" />
            <Skeleton className="library-skeleton-date is-short" />
          </div>
          <div className="library-skeleton-times">
            <Skeleton className="library-skeleton-date" />
            <Skeleton className="library-skeleton-date" />
          </div>
          <Skeleton className="library-skeleton-action is-block" />
        </div>
      ))}
    </SkeletonStatus>
  );
}

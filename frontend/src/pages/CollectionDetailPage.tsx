import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { ApiError, api, isTerminalStatus } from "../api/client";
import type { LibraryEntity, LibraryMetadata, LibrarySearchResult, Task } from "../api/types";
import { ChevronIcon, CollectionIcon, FolderPlusIcon } from "../app/icons";
import { AppLink } from "../app/router";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InlineNotice } from "../components/InlineNotice";
import { OutputExportDialog } from "../components/OutputExportDialog";
import { Skeleton, SkeletonStatus } from "../components/Skeleton";
import { CollectionTaskPickerDialog } from "../features/collection-management/CollectionTaskPickerDialog";
import { TaskLibraryRow, taskLibraryColumnTemplate } from "../features/task-library/TaskLibraryRow";
import { taskTitle } from "../features/task-library/model";
import type { LibraryProperty } from "../features/task-library/types";

const collectionProperties = new Set<LibraryProperty>(["cover", "title", "publishedAt"]);

export function CollectionDetailPage({ collectionId }: { collectionId: string }) {
  const [collection, setCollection] = useState<LibraryEntity | null>(null);
  const [results, setResults] = useState<LibrarySearchResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const [removeTargets, setRemoveTargets] = useState<Task[]>([]);
  const [removing, setRemoving] = useState(false);
  const [exportTask, setExportTask] = useState<Task | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams({ limit: "100", collection_id: collectionId });
    try {
      const [metadata, collectionResults] = await Promise.all([
        api.request<LibraryMetadata>("/api/library/meta"),
        api.request<LibrarySearchResult[]>(`/api/library/search?${query.toString()}`),
      ]);
      const current = metadata.collections.find((item) => item.id === collectionId) ?? null;
      setCollection(current);
      setResults(current ? collectionResults : []);
      setSelectedTaskIds(new Set());
      if (!current) setError("合集不存在。");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法读取合集。");
    } finally {
      setLoading(false);
    }
  }, [collectionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const tasks = useMemo(() => results.map((result) => result.task), [results]);
  const currentTaskIds = useMemo(() => new Set(tasks.map((task) => task.id)), [tasks]);
  const selectableTaskIds = useMemo(
    () => tasks.filter((task) => isTerminalStatus(task.status)).map((task) => task.id),
    [tasks],
  );
  const allSelected = selectableTaskIds.length > 0 && selectableTaskIds.every((id) => selectedTaskIds.has(id));

  const toggleTask = (taskId: string) => {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  };

  const removeFromCollection = async () => {
    if (!collection || removeTargets.length === 0 || removing) return;
    setRemoving(true);
    setError(null);
    try {
      await api.request("/api/library/organize", {
        method: "POST",
        body: {
          task_ids: removeTargets.map((task) => task.id),
          collection_ids: [collection.id],
          tag_ids: [],
          operation: "remove",
        },
      });
      setRemoveTargets([]);
      await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法移出合集。");
    } finally {
      setRemoving(false);
    }
  };

  if (loading) return <CollectionDetailSkeleton />;

  return (
    <div className="page collection-detail-page">
      <nav className="collection-detail-breadcrumb" aria-label="面包屑">
        <AppLink to="/collections">我的合集</AppLink>
        <ChevronIcon />
        <span>{collection?.name ?? "合集"}</span>
      </nav>

      {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}

      {collection ? (
        <>
          <header className="collection-detail-header">
            <div>
              <CollectionIcon />
              <h1>{collection.name}</h1>
            </div>
            <button type="button" className="button collection-add-button" onClick={() => setPickerOpen(true)}>
              <FolderPlusIcon />
              批量添加
            </button>
          </header>

          <section className="collection-detail-controls" aria-label="合集内容操作">
            <strong>{tasks.length} 项内容</strong>
            {tasks.length > 0 ? (
              <div>
                <label className="collection-select-all">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => setSelectedTaskIds(allSelected ? new Set() : new Set(selectableTaskIds))}
                  />
                  批量选择
                </label>
                {selectedTaskIds.size > 0 ? (
                  <button
                    type="button"
                    className="button button-quiet collection-remove-selected"
                    onClick={() => setRemoveTargets(tasks.filter((task) => selectedTaskIds.has(task.id)))}
                  >
                    移出合集（{selectedTaskIds.size}）
                  </button>
                ) : null}
              </div>
            ) : null}
          </section>

          {tasks.length > 0 ? (
            <div
              className="collection-detail-list"
              style={{ "--library-row-columns": taskLibraryColumnTemplate(collectionProperties) } as CSSProperties}
            >
              {results.map((result) => (
                <TaskLibraryRow
                  key={result.task.id}
                  task={result.task}
                  selected={selectedTaskIds.has(result.task.id)}
                  view="table"
                  properties={collectionProperties}
                  tags={result.tags}
                  onSelect={(taskId) => toggleTask(taskId)}
                  onExport={setExportTask}
                  onDelete={(task) => setRemoveTargets([task])}
                  deleteLabel="移出合集"
                />
              ))}
            </div>
          ) : (
            <div className="collection-detail-empty">
              <CollectionIcon />
              <h2>这个合集还没有内容</h2>
              <button type="button" className="button button-primary" onClick={() => setPickerOpen(true)}>
                添加内容
              </button>
            </div>
          )}

          <CollectionTaskPickerDialog
            open={pickerOpen}
            collectionId={collection.id}
            currentTaskIds={currentTaskIds}
            onClose={() => setPickerOpen(false)}
            onAdded={() => void load()}
          />

          {exportTask?.items[0] ? (
            <OutputExportDialog
              itemId={exportTask.items[0].id}
              title={taskTitle(exportTask)}
              open={Boolean(exportTask)}
              onClose={() => setExportTask(null)}
            />
          ) : null}

          <ConfirmDialog
            open={removeTargets.length > 0}
            title={removeTargets.length > 1 ? `移出 ${removeTargets.length} 项内容？` : "移出合集？"}
            description={`只会从“${collection.name}”中移除，原总结记录会保留。`}
            confirmLabel="移出合集"
            busy={removing}
            onConfirm={() => void removeFromCollection()}
            onClose={() => {
              if (!removing) setRemoveTargets([]);
            }}
          />
        </>
      ) : null}
    </div>
  );
}

function CollectionDetailSkeleton() {
  return (
    <SkeletonStatus className="page collection-detail-page collection-detail-skeleton" label="正在读取合集">
      <Skeleton className="collection-detail-skeleton-breadcrumb" />
      <Skeleton className="collection-detail-skeleton-title" />
      <Skeleton className="collection-detail-skeleton-control" />
      {Array.from({ length: 3 }, (_, index) => (
        <Skeleton className="collection-detail-skeleton-row" key={index} />
      ))}
    </SkeletonStatus>
  );
}

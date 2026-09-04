import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { LibraryEntity, LibraryMetadata } from "../api/types";
import {
  ChevronIcon,
  CollectionIcon,
  EditIcon,
  FolderIcon,
  FolderPlusIcon,
  PackageIcon,
  TasksIcon,
  TrashIcon,
} from "../app/icons";
import { AppLink } from "../app/router";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { FormDialog } from "../components/FormDialog";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import { Skeleton, SkeletonStatus } from "../components/Skeleton";

const emptyMetadata: LibraryMetadata = {
  collections: [],
  tags: [],
  total_count: 0,
  unclassified_count: 0,
};

function taskCountLabel(count = 0): string {
  return `${count} 个视频`;
}

function collectionTarget(collectionId: string): string {
  return `/collections/${encodeURIComponent(collectionId)}`;
}

function CollectionPageSkeleton() {
  return (
    <SkeletonStatus className="collection-page-skeleton" label="正在读取合集">
      <section className="collection-folder-panel">
        <div className="collection-section-heading">
          <Skeleton className="collection-skeleton-label" />
          <Skeleton className="collection-skeleton-button" />
        </div>
        {Array.from({ length: 2 }, (_, index) => (
          <Skeleton className="collection-skeleton-row" key={index} />
        ))}
      </section>
      <div className="collection-overview-grid">
        <Skeleton className="collection-skeleton-overview" />
        <Skeleton className="collection-skeleton-overview" />
      </div>
      <div className="collection-card-grid">
        <Skeleton className="collection-skeleton-card" />
        <Skeleton className="collection-skeleton-card" />
      </div>
    </SkeletonStatus>
  );
}

export function CollectionManagementPage() {
  const [metadata, setMetadata] = useState<LibraryMetadata>(emptyMetadata);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "rename" | null>(null);
  const [editing, setEditing] = useState<LibraryEntity | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<LibraryEntity | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadMetadata = useCallback(async () => {
    setError(null);
    try {
      const value = await api.request<LibraryMetadata>("/api/library/meta");
      setMetadata(value);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法读取合集。请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMetadata();
  }, [loadMetadata]);

  const collections = useMemo(
    () => [...metadata.collections].sort((left, right) => left.name.localeCompare(right.name, "zh-CN")),
    [metadata.collections],
  );

  const openCreate = () => {
    setEditing(null);
    setName("");
    setDialogMode("create");
  };

  const openRename = (collection: LibraryEntity) => {
    setEditing(collection);
    setName(collection.name);
    setDialogMode("rename");
  };

  const saveCollection = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = name.trim();
    if (!normalized || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (dialogMode === "rename" && editing) {
        await api.request<LibraryEntity>(`/api/library/collections/${editing.id}`, {
          method: "PATCH",
          body: { name: normalized },
        });
      } else {
        await api.request<LibraryEntity>("/api/library/collections", {
          method: "POST",
          body: { name: normalized },
        });
      }
      setDialogMode(null);
      await loadMetadata();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法保存合集。请稍后重试。");
    } finally {
      setSaving(false);
    }
  };

  const deleteCollection = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await api.request<void>(`/api/library/collections/${deleteTarget.id}`, {
        method: "DELETE",
      });
      setDeleteTarget(null);
      await loadMetadata();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "无法删除合集。请稍后重试。");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="page collection-management-page">
      <header className="collection-page-header">
        <h1>我创建的合集</h1>
      </header>

      <MotionPresence present={Boolean(error)}>
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      </MotionPresence>

      {loading ? (
        <CollectionPageSkeleton />
      ) : (
        <>
          <section className="collection-folder-panel" aria-labelledby="collection-folders-title">
            <div className="collection-section-heading">
              <h2 id="collection-folders-title"><FolderIcon />文件夹</h2>
              <button type="button" className="button button-quiet" onClick={openCreate}>
                <FolderPlusIcon />
                新建文件夹
              </button>
            </div>
            <div className="collection-folder-list">
              {collections.length > 0 ? collections.map((collection) => (
                <AppLink
                  key={collection.id}
                  to={collectionTarget(collection.id)}
                  className="collection-folder-row"
                >
                  <CollectionIcon />
                  <span>{collection.name}</span>
                  <small>{taskCountLabel(collection.task_count)}</small>
                  <ChevronIcon />
                </AppLink>
              )) : (
                <div className="collection-empty-folder">
                  <span>还没有文件夹</span>
                  <button type="button" className="button button-quiet" onClick={openCreate}>创建第一个合集</button>
                </div>
              )}
            </div>
          </section>

          <section className="collection-overview-grid" aria-label="合集快捷入口">
            <AppLink to="/tasks" className="collection-overview-card">
              <span className="collection-overview-icon is-all"><TasksIcon /></span>
              <span><strong>所有总结</strong><small>{taskCountLabel(metadata.total_count)}</small></span>
              <ChevronIcon />
            </AppLink>
            <AppLink to="/tasks?unclassified=true" className="collection-overview-card">
              <span className="collection-overview-icon is-unclassified"><PackageIcon /></span>
              <span><strong>未分类</strong><small>{taskCountLabel(metadata.unclassified_count)}</small></span>
              <ChevronIcon />
            </AppLink>
          </section>

          <section className="collection-card-section" aria-labelledby="collection-cards-title">
            <div className="collection-card-section-heading">
              <h2 id="collection-cards-title">按主题浏览</h2>
              <span>{collections.length} 个合集</span>
            </div>
            {collections.length > 0 ? (
              <div className="collection-card-grid">
                {collections.map((collection, index) => (
                  <article className="collection-card" key={collection.id}>
                    <AppLink to={collectionTarget(collection.id)} className="collection-card-link" aria-label={`打开合集 ${collection.name}`}>
                      <div className="collection-card-cover" data-variant={String(index % 4)}>
                        <strong>{collection.name.slice(0, 12)}</strong>
                        <CollectionIcon />
                      </div>
                      <div className="collection-card-copy">
                        <span><strong>{collection.name}</strong><small>{taskCountLabel(collection.task_count)}</small></span>
                        <ChevronIcon />
                      </div>
                    </AppLink>
                    <div className="collection-card-actions" aria-label={`${collection.name} 操作`}>
                      <button type="button" className="icon-button" aria-label={`重命名 ${collection.name}`} onClick={() => openRename(collection)}>
                        <EditIcon />
                      </button>
                      <button type="button" className="icon-button collection-delete-action" aria-label={`删除 ${collection.name}`} onClick={() => setDeleteTarget(collection)}>
                        <TrashIcon />
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="collection-card-empty">
                <CollectionIcon />
                <h3>还没有合集</h3>
                <button type="button" className="button button-primary" onClick={openCreate}>
                  <FolderPlusIcon />新建文件夹
                </button>
              </div>
            )}
          </section>
        </>
      )}

      <FormDialog
        open={dialogMode !== null}
        title={dialogMode === "rename" ? "重命名合集" : "创建新合集"}
        busy={saving}
        onClose={() => setDialogMode(null)}
      >
        <form className="settings-dialog-form" onSubmit={saveCollection}>
          <div className="field">
            <label className="field-label" htmlFor="collection-name">名称</label>
            <input
              id="collection-name"
              className="text-input"
              value={name}
              maxLength={128}
              autoFocus
              placeholder="例如：租房攻略"
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="actions dialog-actions">
            <button type="button" className="button" disabled={saving} onClick={() => setDialogMode(null)}>取消</button>
            <button type="submit" className="button button-primary" disabled={saving || !name.trim()}>
              {saving ? "保存中…" : dialogMode === "rename" ? "保存名称" : "创建合集"}
            </button>
          </div>
        </form>
      </FormDialog>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        title="删除合集"
        description={<>确定删除“{deleteTarget?.name}”吗？合集中的总结记录不会被删除。</>}
        confirmLabel="删除合集"
        danger
        busy={deleting}
        onConfirm={() => void deleteCollection()}
        onClose={() => setDeleteTarget(null)}
      />
    </div>
  );
}

import { useEffect, useId, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { LibraryEntity, LibraryMetadata } from "../../api/types";
import { FolderIcon, TagIcon, TrashIcon } from "../../app/icons";
import { InlineNotice } from "../../components/InlineNotice";
import { ModalDialog } from "../../components/ModalDialog";

type Kind = "collections" | "tags";

export function OrganizeDialog({
  open,
  taskIds,
  metadata,
  onMetadata,
  onClose,
  onApplied,
  collectionsOnly = false,
}: {
  open: boolean;
  taskIds: string[];
  metadata: LibraryMetadata;
  onMetadata: (next: LibraryMetadata) => void;
  onClose: () => void;
  onApplied: () => void;
  collectionsOnly?: boolean;
}) {
  const [selectedCollections, setSelectedCollections] = useState<Set<string>>(new Set());
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [newCollection, setNewCollection] = useState("");
  const [newTag, setNewTag] = useState("");
  const [collectionMode, setCollectionMode] = useState<"new" | "existing">("new");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    setCollectionMode("new");
    setSelectedCollections(new Set());
    setSelectedTags(new Set());
    setNewCollection("");
    setNewTag("");
    setError(null);
  }, [open]);

  const create = async (kind: Kind, name: string): Promise<LibraryEntity | null> => {
    if (!name.trim()) return null;
    setBusy(true);
    setError(null);
    try {
      const created = await api.request<LibraryEntity>(`/api/library/${kind}`, {
        method: "POST",
        body: { name },
      });
      onMetadata({ ...metadata, [kind]: [...metadata[kind], created] });
      if (kind === "collections") {
        setSelectedCollections((current) => new Set(current).add(created.id));
        setNewCollection("");
      } else {
        setSelectedTags((current) => new Set(current).add(created.id));
        setNewTag("");
      }
      return created;
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "创建失败。");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const removeEntity = async (kind: Kind, id: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.request<void>(`/api/library/${kind}/${id}`, { method: "DELETE" });
      onMetadata({ ...metadata, [kind]: metadata[kind].filter((item) => item.id !== id) });
      if (kind === "collections") {
        setSelectedCollections((current) => new Set([...current].filter((value) => value !== id)));
      } else {
        setSelectedTags((current) => new Set([...current].filter((value) => value !== id)));
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "删除失败。");
    } finally {
      setBusy(false);
    }
  };

  const apply = async (
    operation: "add" | "remove",
    collectionIds = [...selectedCollections],
    tagIds = [...selectedTags],
  ) => {
    setBusy(true);
    setError(null);
    try {
      await api.request("/api/library/organize", {
        method: "POST",
        body: {
          task_ids: taskIds,
          collection_ids: collectionIds,
          tag_ids: tagIds,
          operation,
        },
      });
      onApplied();
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "整理失败。");
    } finally {
      setBusy(false);
    }
  };

  const createCollectionAndApply = async () => {
    const created = await create("collections", newCollection);
    if (created) await apply("add", [created.id], []);
  };

  const entityList = (
    kind: Kind,
    items: LibraryEntity[],
    selected: Set<string>,
    setSelected: (next: Set<string>) => void,
  ) => (
    <div className="organize-entities">
      {items.map((item) => (
        <div className="organize-entity" key={item.id}>
          <label>
            <input
              type="checkbox"
              checked={selected.has(item.id)}
              onChange={() => {
                const next = new Set(selected);
                if (next.has(item.id)) next.delete(item.id);
                else next.add(item.id);
                setSelected(next);
              }}
            />
            {item.name}
          </label>
          <button
            type="button"
            className="icon-button destructive-icon-button"
            aria-label={`删除${item.name}`}
            disabled={busy}
            onClick={() => void removeEntity(kind, item.id)}
          >
            <TrashIcon />
          </button>
        </div>
      ))}
    </div>
  );

  return (
    <ModalDialog
      open={open}
      busy={busy}
      className="organize-dialog"
      labelledBy={titleId}
      onClose={onClose}
    >
        <header className="dialog-heading">
          <h2 id={titleId}>{collectionsOnly ? "添加到合集" : `整理 ${taskIds.length} 项`}</h2>
          <button className="icon-button" type="button" aria-label="关闭" disabled={busy} onClick={onClose}>×</button>
        </header>
        {error && <InlineNotice tone="danger">{error}</InlineNotice>}
        {collectionsOnly ? (
          <>
            <div className="collection-mode-options" role="radiogroup" aria-label="添加方式">
              <label className="collection-mode-option">
                <input
                  type="radio"
                  name="collection-mode"
                  checked={collectionMode === "new"}
                  onChange={() => setCollectionMode("new")}
                />
                <FolderIcon />
                <strong>创建新合集</strong>
              </label>
              <label className="collection-mode-option">
                <input
                  type="radio"
                  name="collection-mode"
                  checked={collectionMode === "existing"}
                  onChange={() => setCollectionMode("existing")}
                />
                <FolderIcon />
                <strong>添加到现有合集</strong>
              </label>
            </div>
            {collectionMode === "new" ? (
              <label className="collection-name-field">
                <span>名称</span>
                <input
                  autoFocus
                  value={newCollection}
                  placeholder="例如：租房与生活"
                  aria-label="新合集名称"
                  onChange={(event) => setNewCollection(event.target.value)}
                />
              </label>
            ) : (
              <section className="existing-collections">
                <h3>选择合集</h3>
                {metadata.collections.length > 0 ? (
                  entityList("collections", metadata.collections, selectedCollections, setSelectedCollections)
                ) : (
                  <p className="muted">还没有合集，请先创建新合集。</p>
                )}
              </section>
            )}
            <footer className="organize-actions">
              <button type="button" className="button button-quiet" disabled={busy} onClick={onClose}>取消</button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || (collectionMode === "new" ? !newCollection.trim() : selectedCollections.size === 0)}
                onClick={() => void (collectionMode === "new" ? createCollectionAndApply() : apply("add"))}
              >
                {collectionMode === "new" ? "创建并添加" : "添加到合集"}
              </button>
            </footer>
          </>
        ) : (
          <section>
            <h3><FolderIcon />合集</h3>
            {entityList("collections", metadata.collections, selectedCollections, setSelectedCollections)}
            <div className="organize-create">
              <input value={newCollection} aria-label="新合集" onChange={(event) => setNewCollection(event.target.value)} />
              <button type="button" className="button" disabled={busy || !newCollection.trim()} onClick={() => void create("collections", newCollection)}>添加</button>
            </div>
          </section>
        )}
        {!collectionsOnly && (
          <section>
            <h3><TagIcon />标签</h3>
            {entityList("tags", metadata.tags, selectedTags, setSelectedTags)}
            <div className="organize-create">
              <input value={newTag} aria-label="新标签" onChange={(event) => setNewTag(event.target.value)} />
              <button type="button" className="button" disabled={busy || !newTag.trim()} onClick={() => void create("tags", newTag)}>添加</button>
            </div>
          </section>
        )}
        {!collectionsOnly && (
          <footer className="organize-actions">
            <button type="button" className="button button-quiet" disabled={busy || (!selectedCollections.size && !selectedTags.size)} onClick={() => void apply("remove")}>移除</button>
            <button type="button" className="button button-primary" disabled={busy || (!selectedCollections.size && !selectedTags.size)} onClick={() => void apply("add")}>应用</button>
          </footer>
        )}
    </ModalDialog>
  );
}

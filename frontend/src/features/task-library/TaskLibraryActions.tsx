import { CheckIcon, FolderIcon, TrashIcon } from "../../app/icons";

type TaskLibraryActionsProps = {
  allSelected: boolean;
  someSelected: boolean;
  selectedCount: number;
  onToggleAll: () => void;
  onOrganize: () => void;
  onDelete: () => void;
};

export function TaskLibraryActions({
  allSelected,
  someSelected,
  selectedCount,
  onToggleAll,
  onOrganize,
  onDelete,
}: TaskLibraryActionsProps) {
  return (
    <div className="library-selection-actions">
      <button
        type="button"
        className={`button button-quiet library-select-all${allSelected ? " is-all-selected" : someSelected ? " is-partial" : ""}`}
        aria-label={allSelected ? "取消选择全部记录" : "选择全部可删除记录"}
        aria-pressed={someSelected && !allSelected ? "mixed" : allSelected}
        onClick={onToggleAll}
      >
        <span className="library-select-state" aria-hidden="true">
          {allSelected && <CheckIcon />}
        </span>
        <span>{selectedCount > 0 ? `已选 ${selectedCount} 项` : "全选"}</span>
      </button>
      <button
        type="button"
        className="button library-organize-button"
        disabled={selectedCount === 0}
        onClick={onOrganize}
      >
        <FolderIcon />
        整理
      </button>
      <button
        type="button"
        className="button button-danger library-bulk-delete"
        disabled={selectedCount === 0}
        onClick={onDelete}
      >
        <TrashIcon />
        删除
      </button>
    </div>
  );
}

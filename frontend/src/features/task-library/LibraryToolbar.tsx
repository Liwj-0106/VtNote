import { useMemo } from "react";
import type { Task } from "../../api/types";
import {
  CheckIcon,
  ChevronDownIcon,
  ColumnsIcon,
  FolderIcon,
  GalleryIcon,
  ListIcon,
  PlusIcon,
  TableIcon,
  TrashIcon,
  WaterfallIcon,
} from "../../app/icons";
import { DropdownMenu } from "../../components/DropdownMenu";
import { AppLink } from "../../app/router";
import { SelectMenu } from "../../components/SelectMenu";
import { SearchField } from "../../components/SearchField";
import { SegmentedTabs } from "../../components/SegmentedTabs";
import type { LibraryFilters } from "../library-discovery/types";
import { BulkExportMenu } from "./BulkExportMenu";
import type { LibraryProperty, LibraryViewMode } from "./types";

const viewChoices: Array<{
  value: LibraryViewMode;
  label: string;
  icon: typeof TableIcon;
}> = [
  { value: "gallery", label: "画廊", icon: GalleryIcon },
  { value: "waterfall", label: "瀑布", icon: WaterfallIcon },
  { value: "list", label: "列表", icon: ListIcon },
  { value: "table", label: "表格", icon: TableIcon },
];

const propertyChoices: Array<{ value: LibraryProperty; label: string }> = [
  { value: "cover", label: "封面" },
  { value: "title", label: "标题" },
  { value: "publishedAt", label: "发布时间" },
];

const statusChoices = [
  { value: "", label: "全部" },
  { value: "completed", label: "已完成" },
  { value: "running", label: "处理中" },
  { value: "failed", label: "失败" },
];

export const LIBRARY_STATUS_TABS_ID = "summary-status-tabs";
export const LIBRARY_RESULTS_PANEL_ID = "summary-library-results";

export function LibraryToolbar({
  filters,
  onFilters,
  view,
  onView,
  properties,
  onToggleProperty,
  selectedTasks,
  selectedVisibleRows,
  visibleRows,
  onAddCollection,
  onDeleteSelected,
  onNew,
  pageSize,
  onPageSize,
  page,
  pageCount,
  canPrevious,
  canNext,
  onPrevious,
  onNext,
  scopeLabel,
}: {
  filters: LibraryFilters;
  onFilters: (filters: LibraryFilters) => void;
  view: LibraryViewMode;
  onView: (view: LibraryViewMode) => void;
  properties: Set<LibraryProperty>;
  onToggleProperty: (property: LibraryProperty) => void;
  selectedTasks: Task[];
  selectedVisibleRows: number;
  visibleRows: number;
  onAddCollection: () => void;
  onDeleteSelected: () => void;
  onNew: () => void;
  pageSize: number;
  onPageSize: (value: number) => void;
  page: number;
  pageCount: number;
  canPrevious: boolean;
  canNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  scopeLabel?: string;
}) {
  const selectedView = useMemo(
    () => viewChoices.find((choice) => choice.value === view) ?? viewChoices.at(-1)!,
    [view],
  );

  const updateFilter = <Key extends keyof LibraryFilters>(
    key: Key,
    value: LibraryFilters[Key],
  ) => onFilters({ ...filters, [key]: value });
  const SelectedViewIcon = selectedView.icon;

  return (
    <>
      <header className="summary-library-header">
        <h1>总结记录</h1>
        <div className="summary-library-header-actions">
          <button
            type="button"
            className="button button-danger summary-library-delete-button"
            disabled={selectedTasks.length === 0}
            onClick={onDeleteSelected}
          >
            <TrashIcon />
            删除
          </button>
          <button type="button" className="button button-primary library-new-button" onClick={onNew}>
            <PlusIcon />
            新增
          </button>
        </div>
      </header>

      <SegmentedTabs
        id={LIBRARY_STATUS_TABS_ID}
        className="summary-status-tabs"
        ariaLabel="总结状态"
        items={statusChoices.map((choice) => ({
          ...choice,
          panelId: LIBRARY_RESULTS_PANEL_ID,
        }))}
        value={filters.status}
        onValueChange={(nextStatus) => updateFilter("status", nextStatus)}
      />

      {scopeLabel ? (
        <div className="library-scope-banner">
          <span>正在查看合集：<strong>{scopeLabel}</strong></span>
          <AppLink to="/tasks">查看全部</AppLink>
        </div>
      ) : null}

      <section className="summary-library-toolbar" aria-label="总结记录工具栏">
        <DropdownMenu
          ariaLabel="展示方式"
          size="compact"
          rootClassName="library-menu-root"
          triggerClassName="button library-toolbar-button library-view-trigger"
          popoverClassName="library-popover library-view-menu"
          trigger={
            <>
              <SelectedViewIcon />
              {selectedView.label}
              <ChevronDownIcon className="dropdown-menu-chevron" />
            </>
          }
        >
          {(close) => (
            <>
              {viewChoices.map((choice) => {
                const Icon = choice.icon;
                return (
                  <button
                    key={choice.value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={view === choice.value}
                    onClick={() => {
                      onView(choice.value);
                      close();
                    }}
                  >
                    <Icon />
                    <span>{choice.label}</span>
                    {view === choice.value && <CheckIcon />}
                  </button>
                );
              })}
            </>
          )}
        </DropdownMenu>

        <SearchField
          className="library-toolbar-search"
          label="搜索总结记录"
          clearLabel="清除总结搜索"
          value={filters.query}
          placeholder="根据标题、字幕或总结搜索"
          onChange={(event) => updateFilter("query", event.currentTarget.value)}
          onClear={() => updateFilter("query", "")}
        />

        <SelectMenu
          ariaLabel="选择来源"
          className="library-source-select"
          value={filters.source}
          onChange={(value) => updateFilter("source", value)}
          options={[
            { value: "", label: "全部来源" },
            { value: "bilibili", label: "B 站" },
            { value: "douyin", label: "抖音" },
            { value: "youtube", label: "YouTube" },
            { value: "uploaded_media", label: "本地音视频" },
            { value: "uploaded_subtitle", label: "本地字幕" },
          ]}
        />

        <button
          type="button"
          className="button library-toolbar-button"
          disabled={selectedTasks.length === 0}
          onClick={onAddCollection}
        >
          <FolderIcon />
          添加到合集
        </button>

        <BulkExportMenu tasks={selectedTasks} />

        <div className="library-pagination" aria-label="分页">
          <span>每页行数</span>
          <SelectMenu
            ariaLabel="每页行数"
            value={String(pageSize)}
            onChange={(value) => onPageSize(Number(value))}
            options={[10, 20, 30, 50].map((value) => ({
              value: String(value),
              label: String(value),
            }))}
          />
          <button type="button" className="button button-quiet" disabled={!canPrevious} onClick={onPrevious}>
            上一页
          </button>
          <span className="library-page-indicator">第 {page + 1} / {pageCount} 页</span>
          <button type="button" className="button button-quiet" disabled={!canNext} onClick={onNext}>
            下一页
          </button>
        </div>

        <DropdownMenu
          ariaLabel="显示属性"
          align="end"
          size="compact"
          rootClassName="library-menu-root"
          triggerClassName="button library-toolbar-button"
          popoverClassName="library-popover library-property-menu"
          trigger={
            <>
              <ColumnsIcon />
              属性
              <ChevronDownIcon className="dropdown-menu-chevron" />
            </>
          }
        >
          {propertyChoices.map((property) => (
            <button
              key={property.value}
              type="button"
              role="menuitemcheckbox"
              aria-checked={properties.has(property.value)}
              onClick={() => onToggleProperty(property.value)}
            >
              <span>{property.label}</span>
              {properties.has(property.value) && <CheckIcon />}
            </button>
          ))}
        </DropdownMenu>
      </section>
      {visibleRows > 0 ? (
        <p className="library-selection-summary" aria-live="polite">
          {selectedVisibleRows}/{visibleRows} 行被选中
        </p>
      ) : null}
    </>
  );
}

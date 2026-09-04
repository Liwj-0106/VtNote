import type { LibraryMetadata } from "../../api/types";
import { SearchField } from "../../components/SearchField";
import { SelectMenu } from "../../components/SelectMenu";
import type { LibraryFilters } from "./types";

export function LibraryDiscoveryBar({
  filters,
  metadata,
  onChange,
}: {
  filters: LibraryFilters;
  metadata: LibraryMetadata;
  onChange: (next: LibraryFilters) => void;
}) {
  const update = <Key extends keyof LibraryFilters>(
    key: Key,
    value: LibraryFilters[Key],
  ) => onChange({ ...filters, [key]: value });

  return (
    <section className="library-discovery" aria-label="搜索与筛选">
      <SearchField
        className="library-search"
        label="搜索内容库"
        clearLabel="清除内容库搜索"
        value={filters.query}
        placeholder="搜索标题、字幕、总结或摘录"
        onChange={(event) => update("query", event.currentTarget.value)}
        onClear={() => update("query", "")}
      />
      <div className="library-filter-grid">
        <SelectMenu
          ariaLabel="来源"
          value={filters.source}
          onChange={(value) => update("source", value)}
          options={[
            { value: "", label: "全部来源" },
            { value: "bilibili", label: "B 站" },
            { value: "douyin", label: "抖音" },
            { value: "youtube", label: "YouTube" },
            { value: "uploaded_media", label: "本地音视频" },
            { value: "uploaded_subtitle", label: "本地字幕" },
          ]}
        />
        <SelectMenu
          ariaLabel="状态"
          value={filters.status}
          onChange={(value) => update("status", value)}
          options={[
            { value: "", label: "全部状态" },
            { value: "completed", label: "完成" },
            { value: "failed", label: "失败" },
            { value: "running", label: "处理中" },
          ]}
        />
        <SelectMenu
          ariaLabel="合集"
          value={filters.collectionId}
          onChange={(value) => update("collectionId", value)}
          options={[
            { value: "", label: "全部合集" },
            ...metadata.collections.map((item) => ({ value: item.id, label: item.name })),
          ]}
        />
        <SelectMenu
          ariaLabel="标签"
          value={filters.tagId}
          onChange={(value) => update("tagId", value)}
          options={[
            { value: "", label: "全部标签" },
            ...metadata.tags.map((item) => ({ value: item.id, label: item.name })),
          ]}
        />
        <label className="library-excerpt-filter">
          <input
            type="checkbox"
            checked={filters.excerptsOnly}
            onChange={(event) => update("excerptsOnly", event.target.checked)}
          />
          仅摘录
        </label>
      </div>
    </section>
  );
}

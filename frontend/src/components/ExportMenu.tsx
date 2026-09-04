import { useState } from "react";
import { ApiError, api } from "../api/client";
import type { SavedExport } from "../api/types";
import { ChevronDownIcon, DownloadIcon } from "../app/icons";
import { DropdownMenu } from "./DropdownMenu";
import { MotionPresence } from "./MotionPresence";

const formats = [
  { value: "srt", label: "SRT 字幕" },
  { value: "vtt", label: "VTT 字幕" },
  { value: "txt", label: "纯文本" },
  { value: "markdown", label: "Markdown" },
  { value: "json", label: "JSON 数据" },
] as const;

export function ExportMenu({
  itemId,
  variant = "original",
  language,
}: {
  itemId: string;
  variant?: "original" | "translation";
  language?: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [savedDirectory, setSavedDirectory] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const download = async (
    format: (typeof formats)[number]["value"],
    close: () => void,
  ) => {
    setError(null);
    setSavedDirectory(null);
    setSaving(true);
    try {
      const result = await api.request<SavedExport>(
        `/api/items/${itemId}/export-text-file`,
        {
          method: "POST",
          body: { variant, format, language },
        },
      );
      setSavedDirectory(result.directory);
      close();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "导出失败，请稍后重试。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="export-menu">
      <DropdownMenu
        ariaLabel="导出格式"
        align="end"
        size="compact"
        rootClassName="export-menu-dropdown"
        triggerClassName="button export-menu-trigger"
        popoverClassName="export-popover"
        disabled={saving}
        trigger={
          <>
            <DownloadIcon />
            导出
            <ChevronDownIcon className="dropdown-menu-chevron export-menu-chevron" />
          </>
        }
      >
        {(close) => (
          <>
            {formats.map((format) => (
              <button
                key={format.value}
                type="button"
                role="menuitem"
                disabled={saving}
                onClick={() => void download(format.value, close)}
              >
                {format.label}
              </button>
            ))}
          </>
        )}
      </DropdownMenu>
      <MotionPresence present={Boolean(savedDirectory)}>
        <p
          className="field-success"
          role="status"
          title={savedDirectory ?? undefined}
        >
          已保存至 {savedDirectory ?? ""}
        </p>
      </MotionPresence>
      <MotionPresence present={Boolean(error)}>
        <p className="field-error">{error ?? ""}</p>
      </MotionPresence>
    </div>
  );
}

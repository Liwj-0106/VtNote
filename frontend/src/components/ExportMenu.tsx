import { useState } from "react";
import { ApiError, api } from "../api/client";
import { DownloadIcon } from "../app/icons";

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
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async (format: (typeof formats)[number]["value"]) => {
    setError(null);
    try {
      const query = new URLSearchParams({ variant, format });
      if (language) query.set("language", language);
      const blob = await api.download(
        `/api/items/${itemId}/export?${query.toString()}`,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `vtnote-${itemId.slice(0, 8)}-${variant}.${
        format === "markdown" ? "md" : format
      }`;
      anchor.click();
      URL.revokeObjectURL(url);
      setOpen(false);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "导出失败，请稍后重试。",
      );
    }
  };

  return (
    <div className="export-menu">
      <button
        type="button"
        className="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        <DownloadIcon />
        导出
      </button>
      {open && (
        <div className="export-popover" role="menu">
          {formats.map((format) => (
            <button
              key={format.value}
              type="button"
              role="menuitem"
              onClick={() => void download(format.value)}
            >
              {format.label}
            </button>
          ))}
        </div>
      )}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

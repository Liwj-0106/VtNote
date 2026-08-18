import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { NoteResult } from "../api/types";
import { loadPreferences, type AppPreferences, type ExportItem } from "../app/preferences";
import { DownloadIcon } from "../app/icons";
import { InlineNotice } from "./InlineNotice";

interface Outcomes {
  audio: boolean;
  transcript: boolean;
  notes: boolean;
}

function safeFilename(title: string): string {
  const cleaned = title.normalize("NFKC").replace(/[\\/:*?"<>|\r\n]+/gu, "-").trim();
  return cleaned.slice(0, 80) || "vtnote-result";
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function plainNote(markdown: string): string {
  return markdown
    .replace(/^---\n[\s\S]*?\n---\n?/u, "")
    .replace(/^#{1,6}\s+/gmu, "")
    .replace(/[*_`>]/gu, "")
    .trim();
}

export function OutputExportDialog({
  itemId,
  title,
  open,
  onClose,
}: {
  itemId: string;
  title: string;
  open: boolean;
  onClose: () => void;
}) {
  const [outcomes, setOutcomes] = useState<Outcomes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ExportItem[]>([]);
  const [exporting, setExporting] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const controller = new AbortController();
    setOutcomes(null);
    setSelected([]);
    setError(null);
    api
      .request<Outcomes>(`/api/items/${itemId}/outcomes`, {
        signal: controller.signal,
      })
      .then((nextOutcomes) => {
        setOutcomes(nextOutcomes);
        setSelected(
          loadPreferences().defaultExportItems.filter((kind) => nextOutcomes[kind]),
        );
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof ApiError ? caught.message : "无法读取可导出内容。");
        }
      });
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      controller.abort();
      document.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [itemId, onClose, open]);

  if (!open) return null;

  const exportOne = async (kind: ExportItem, preferences: AppPreferences) => {
    if (kind === "audio") {
      const blob = await api.download(
        `/api/items/${itemId}/audio?format=${preferences.audioFormat}`,
      );
      downloadBlob(blob, `${safeFilename(title)}.${preferences.audioFormat}`);
    } else if (kind === "transcript") {
      const format = preferences.subtitleFormat;
      const blob = await api.download(
        `/api/items/${itemId}/export?variant=original&format=${format}`,
      );
      downloadBlob(blob, `${safeFilename(title)}.${format}`);
    } else {
      const notes = await api.request<NoteResult[]>(`/api/items/${itemId}/notes`);
      const markdown = notes[notes.length - 1]?.markdown ?? "";
      const isText = preferences.noteFormat === "txt";
      downloadBlob(
        new Blob([isText ? plainNote(markdown) : markdown], {
          type: isText ? "text/plain;charset=utf-8" : "text/markdown;charset=utf-8",
        }),
        `${safeFilename(title)}.${isText ? "txt" : "md"}`,
      );
    }
  };

  const runSelectedExports = async () => {
    setExporting(true);
    setError(null);
    try {
      const preferences = loadPreferences();
      for (const kind of selected) {
        await exportOne(kind, preferences);
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "导出失败，请稍后重试。");
    } finally {
      setExporting(false);
    }
  };

  const tiles = [
    { kind: "audio" as const, label: "音频", hint: "M4A / MP3" },
    { kind: "transcript" as const, label: "字幕原文", hint: "SRT / TXT" },
    { kind: "notes" as const, label: "AI 笔记", hint: "Markdown / TXT" },
  ];

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <dialog
        className="export-dialog"
        open
        aria-labelledby="export-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <p className="page-kicker">Export</p>
            <h2 id="export-title">导出当前结果</h2>
            <p>{title}</p>
          </div>
          <button
            ref={closeButton}
            type="button"
            className="icon-button dialog-close"
            aria-label="关闭导出弹窗"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        {error && <InlineNotice tone="danger">{error}</InlineNotice>}
        <div className="export-choice-grid">
          {tiles.map((tile) => {
            const available = Boolean(outcomes?.[tile.kind]);
            const checked = selected.includes(tile.kind);
            return (
              <label key={tile.kind} className="export-choice">
                <input
                  type="checkbox"
                  aria-label={tile.label}
                  checked={checked}
                  disabled={!available || exporting}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, tile.kind]
                        : current.filter((kind) => kind !== tile.kind),
                    )
                  }
                />
                <DownloadIcon />
                <span>
                  <strong>{tile.label}</strong>
                  <small>
                    {outcomes === null
                      ? "正在检查…"
                      : available
                        ? tile.hint
                        : "当前没有此结果"}
                  </small>
                </span>
              </label>
            );
          })}
        </div>
        <button
          type="button"
          className="primary-button export-confirm-button"
          disabled={outcomes === null || selected.length === 0 || exporting}
          onClick={() => void runSelectedExports()}
        >
          导出所选（{selected.length}）
        </button>
      </dialog>
    </div>
  );
}

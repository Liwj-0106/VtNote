import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SavedExport } from "../api/types";
import { loadPreferences, type ExportItem } from "../app/preferences";
import { DownloadIcon } from "../app/icons";
import { InlineNotice } from "./InlineNotice";
import { ModalDialog } from "./ModalDialog";
import { Skeleton } from "./Skeleton";

interface Outcomes {
  audio: boolean;
  transcript: boolean;
  notes: boolean;
}

export function OutputExportDialog({
  itemId,
  title,
  open,
  onClose,
  onExited,
}: {
  itemId: string;
  title: string;
  open: boolean;
  onClose: () => void;
  onExited?: () => void;
}) {
  const [outcomes, setOutcomes] = useState<Outcomes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ExportItem[]>([]);
  const [exporting, setExporting] = useState(false);
  const [savedDirectory, setSavedDirectory] = useState<string | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setOutcomes(null);
    setSelected([]);
    setError(null);
    setSavedDirectory(null);
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
    return () => {
      controller.abort();
    };
  }, [itemId, open]);

  const runSelectedExports = async () => {
    setExporting(true);
    setError(null);
    try {
      const preferences = loadPreferences();
      const saved = await api.request<SavedExport>(`/api/items/${itemId}/export-files`, {
        method: "POST",
        body: {
          items: selected,
          audio_format: preferences.audioFormat,
          transcript_format: preferences.subtitleFormat,
          note_format: preferences.noteFormat,
        },
      });
      setSavedDirectory(saved.directory);
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
    <ModalDialog
      open={open}
      busy={exporting}
      className="export-dialog"
      labelledBy={titleId}
      initialFocusRef={closeButton}
      onClose={onClose}
      onExited={onExited}
    >
        <div className="dialog-heading">
          <div>
            <p className="page-kicker">Export</p>
            <h2 id={titleId}>导出当前结果</h2>
            <p>{title}</p>
          </div>
          <button
            ref={closeButton}
            type="button"
            className="icon-button dialog-close"
            aria-label="关闭导出弹窗"
            disabled={exporting}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        {error && <InlineNotice tone="danger">{error}</InlineNotice>}
        {savedDirectory && (
          <p className="export-saved-directory" role="status" title={savedDirectory}>
            {savedDirectory}
          </p>
        )}
        <div
          className="export-choice-grid"
          aria-busy={outcomes === null}
          aria-label={outcomes === null ? "正在检查可导出内容" : undefined}
        >
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
                    {outcomes === null ? (
                      <Skeleton className="export-choice-hint-skeleton" />
                    ) : available ? (
                      tile.hint
                    ) : (
                      "当前没有此结果"
                    )}
                  </small>
                </span>
              </label>
            );
          })}
        </div>
        <button
          type="button"
          className="primary-button export-confirm-button"
          disabled={outcomes === null || selected.length === 0 || exporting || Boolean(savedDirectory)}
          onClick={() => void runSelectedExports()}
        >
          {savedDirectory ? "已导出" : `导出所选（${selected.length}）`}
        </button>
    </ModalDialog>
  );
}

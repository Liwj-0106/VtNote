import { useState } from "react";
import { ApiError, api } from "../../api/client";
import type { SavedExport, Task } from "../../api/types";
import {
  ChevronDownIcon,
  DownloadIcon,
  PackageIcon,
} from "../../app/icons";
import { DropdownMenu } from "../../components/DropdownMenu";
import { useTaskQueue } from "../task-queue/TaskQueueProvider";

type BatchExportMode =
  | "summary_markdown"
  | "original_markdown"
  | "zip_all"
  | "zip_notes";

const choices: Array<{
  mode: BatchExportMode;
  label: string;
  hint: string;
  archive: boolean;
}> = [
  {
    mode: "summary_markdown",
    label: "导出总结（Markdown）",
    hint: "每条记录保存一个 Markdown 文件",
    archive: false,
  },
  {
    mode: "original_markdown",
    label: "导出原文（Markdown）",
    hint: "每条记录保存一个原文 Markdown 文件",
    archive: false,
  },
  {
    mode: "zip_all",
    label: "导出总结和原文（ZIP）",
    hint: "打包可用的总结和原文",
    archive: true,
  },
  {
    mode: "zip_notes",
    label: "导出 ZIP（仅总结）",
    hint: "仅打包总结 Markdown",
    archive: true,
  },
];

export function BulkExportMenu({ tasks }: { tasks: Task[] }) {
  const { notify } = useTaskQueue();
  const [busy, setBusy] = useState(false);
  const runExport = async (mode: BatchExportMode, close: () => void) => {
    if (tasks.length === 0 || busy) return;
    setBusy(true);
    try {
      const result = await api.request<SavedExport>("/api/tasks/bulk-export", {
        method: "POST",
        body: { task_ids: tasks.map((task) => task.id), mode },
      });
      notify(`已保存至 ${result.directory}`);
      close();
    } catch (caught) {
      notify(
        caught instanceof ApiError ? caught.message : "批量导出失败。",
        "danger",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="library-menu-root bulk-export-root">
      <DropdownMenu
        ariaLabel="批量导出"
        size="wide"
        rootClassName="bulk-export-dropdown"
        triggerClassName="button library-toolbar-button"
        popoverClassName="library-popover bulk-export-popover"
        disabled={tasks.length === 0 || busy}
        trigger={
          <>
            <DownloadIcon />
            批量导出
            <ChevronDownIcon className="dropdown-menu-chevron" />
          </>
        }
      >
        {(close) => (
          <>
            {choices.map((choice) => (
              <button
                key={choice.mode}
                type="button"
                role="menuitem"
                disabled={busy}
                onClick={() => void runExport(choice.mode, close)}
              >
                {choice.archive ? <PackageIcon /> : <DownloadIcon />}
                <span>
                  <strong>{choice.label}</strong>
                  <small>{choice.hint}</small>
                </span>
              </button>
            ))}
          </>
        )}
      </DropdownMenu>
    </div>
  );
}

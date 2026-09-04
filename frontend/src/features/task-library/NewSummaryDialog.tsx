import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type {
  BatchSourceProbe,
  DefaultsView,
  ProfileView,
  SourceProbe,
  Task,
} from "../../api/types";
import {
  ClipboardIcon,
  CloseIcon,
  LinkIcon,
  SendIcon,
  SettingsIcon,
  SpinnerIcon,
  UploadIcon,
} from "../../app/icons";
import { FilePicker } from "../../components/FilePicker";
import { InlineNotice } from "../../components/InlineNotice";
import { ModalDialog } from "../../components/ModalDialog";
import {
  SegmentedTabs,
  segmentedTabId,
  type SegmentedTabItem,
} from "../../components/SegmentedTabs";
import { Skeleton } from "../../components/Skeleton";
import {
  SummarySettingsDialog,
} from "../task-creation/SummarySettingsDialog";
import {
  buildTaskCreationOptions,
  extractSourceUrls,
  isSubtitleFile,
} from "../task-creation/model";
import { createUrlTasksInBatches } from "../task-creation/createBatchTasks";
import {
  availableNotesProfiles,
  initialSummarySettings,
  type SummaryTaskSettings,
} from "../summary-settings/model";
import { useTaskQueue } from "../task-queue/TaskQueueProvider";

type SourceMode = "url" | "upload";
const NEW_SUMMARY_OUTPUTS = ["audio", "transcript", "notes"] as const;
const NEW_SUMMARY_SOURCE_TABS_ID = "new-summary-source-tabs";
const NEW_SUMMARY_SOURCE_PANEL_ID = "new-summary-source-panel";
const newSummarySourceTabs: readonly SegmentedTabItem<SourceMode>[] = [
  {
    value: "url",
    label: <><LinkIcon />链接</>,
    panelId: NEW_SUMMARY_SOURCE_PANEL_ID,
  },
  {
    value: "upload",
    label: <><UploadIcon />上传</>,
    panelId: NEW_SUMMARY_SOURCE_PANEL_ID,
  },
];

function summaryDialogError(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message;
  if (caught instanceof DOMException && caught.name === "AbortError") {
    return "上传已取消。";
  }
  return "无法创建总结，请稍后重试。";
}

export function NewSummaryDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify, registerTasks } = useTaskQueue();
  const [sourceMode, setSourceMode] = useState<SourceMode>("url");
  const [sourceText, setSourceText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [readingClipboard, setReadingClipboard] = useState(false);
  const [summarySettingsOpen, setSummarySettingsOpen] = useState(false);
  const [summarySettings, setSummarySettings] = useState<SummaryTaskSettings>({
    enabled: true,
    profileId: "",
    outputLanguage: "zh-Hans",
  });
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setSourceMode("url");
    setSourceText("");
    setFiles([]);
    setSummarySettingsOpen(false);
    setSummarySettings({
      enabled: true,
      profileId: "",
      outputLanguage: "zh-Hans",
    });
    setError(null);
    setLoading(true);
    Promise.all([
      api.request<DefaultsView>("/api/defaults", { signal: controller.signal }),
      api.request<ProfileView[]>("/api/profiles", { signal: controller.signal }),
    ])
      .then(([nextDefaults, nextProfiles]) => {
        setDefaults(nextDefaults);
        setProfiles(nextProfiles);
        setSummarySettings(
          initialSummarySettings(nextDefaults, nextProfiles, true),
        );
        window.requestAnimationFrame(() => inputRef.current?.focus());
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(summaryDialogError(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open]);

  const notesProfiles = useMemo(
    () => availableNotesProfiles(profiles),
    [profiles],
  );

  const taskOptions = (subtitleUpload = false) => {
    return buildTaskCreationOptions({
      defaults,
      settings: summarySettings,
      selectedOutputs: NEW_SUMMARY_OUTPUTS,
      subtitleUpload,
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (
      submitting ||
      loading ||
      (summarySettings.enabled && !summarySettings.profileId)
    ) {
      return;
    }
    const urls = extractSourceUrls(sourceText);
    if (sourceMode === "url" && urls.length === 0) return;
    if (sourceMode === "upload" && files.length === 0) return;

    setSubmitting(true);
    setError(null);
    try {
      const createdTasks: Task[] = [];
      if (sourceMode === "url") {
        if (urls.length > 1) {
          const batch = await api.request<BatchSourceProbe>("/api/sources/probe-batch", {
            method: "POST",
            body: { text: sourceText },
          });
          if (batch.valid_sources.length === 0) {
            throw new Error("no valid sources");
          }
          const created = await createUrlTasksInBatches(
            batch.valid_sources.map((source) => source.url),
            taskOptions(),
          );
          createdTasks.push(...created);
        } else {
          const probe = await api.request<SourceProbe>("/api/sources/probe", {
            method: "POST",
            body: { url: urls[0] },
          });
          if (probe.result_type === "collection" && probe.collection) {
            const created = await createUrlTasksInBatches(
              probe.collection.items.map((item) => item.canonical_url),
              taskOptions(),
            );
            createdTasks.push(...created);
          } else {
            createdTasks.push(
              await api.request<Task>("/api/tasks", {
                method: "POST",
                body: {
                  sources: [{ kind: "url", locator: probe.canonical_url }],
                  ...taskOptions(),
                },
              }),
            );
          }
        }
      } else {
        for (const file of files) {
          const subtitleUpload = isSubtitleFile(file);
          createdTasks.push(
            await api.uploadTask<Task>(file, {
              kind: subtitleUpload ? "subtitle" : "media",
              ...taskOptions(subtitleUpload),
            }),
          );
        }
      }
      registerTasks(createdTasks);
      notify(createdTasks.length === 1 ? "总结任务已创建" : `已创建 ${createdTasks.length} 个总结任务`);
      onCreated();
      onClose();
    } catch (caught) {
      setError(summaryDialogError(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit =
    !loading &&
    !submitting &&
    (!summarySettings.enabled || Boolean(summarySettings.profileId)) &&
    (sourceMode === "url" ? extractSourceUrls(sourceText).length > 0 : files.length > 0);

  const pasteSourceText = async () => {
    setReadingClipboard(true);
    setError(null);
    try {
      const clipboardText = await navigator.clipboard.readText();
      if (!clipboardText) return;
      setSourceText(clipboardText);
      inputRef.current?.focus();
    } catch {
      setError("无法读取剪贴板，请检查浏览器剪贴板权限。");
    } finally {
      setReadingClipboard(false);
    }
  };

  return (
    <>
      <ModalDialog
        open={open}
        busy={submitting || summarySettingsOpen}
        className="new-summary-dialog"
        labelledBy="new-summary-title"
        onClose={onClose}
      >
          <header className="dialog-heading">
            <div>
              <h2 id="new-summary-title">把音视频变成字幕和笔记</h2>
              <p>支持 B 站、抖音、YouTube 链接和本地文件。</p>
            </div>
            <button type="button" className="icon-button" aria-label="关闭新增总结" disabled={submitting} onClick={onClose}>
              <CloseIcon />
            </button>
          </header>

          <form className="launcher new-summary-form" onSubmit={submit}>
            <SegmentedTabs
              id={NEW_SUMMARY_SOURCE_TABS_ID}
              className="launcher-tabs"
              ariaLabel="输入方式"
              items={newSummarySourceTabs}
              value={sourceMode}
              onValueChange={(nextMode) => {
                setSourceMode(nextMode);
                setError(null);
              }}
            />

            <div className="launcher-tools" aria-label="处理设置">
              <button
                type="button"
                className="launcher-settings-button"
                aria-haspopup="dialog"
                disabled={loading || submitting}
                onClick={() => setSummarySettingsOpen(true)}
              >
                <SettingsIcon />
                总结设置
              </button>
            </div>

            {loading ? (
              <div
                id={NEW_SUMMARY_SOURCE_PANEL_ID}
                className="new-summary-skeleton"
                role="tabpanel"
                aria-label="正在准备新增总结"
                aria-labelledby={segmentedTabId(NEW_SUMMARY_SOURCE_TABS_ID, sourceMode)}
              >
                <Skeleton />
                <Skeleton />
                <Skeleton className="is-block" />
              </div>
            ) : (
              <div
                id={NEW_SUMMARY_SOURCE_PANEL_ID}
                className="launcher-input new-summary-input"
                data-source-mode={sourceMode === "url" ? "url" : "media"}
                role="tabpanel"
                aria-labelledby={segmentedTabId(NEW_SUMMARY_SOURCE_TABS_ID, sourceMode)}
              >
                <div className="launcher-source">
                  {sourceMode === "url" ? (
                    <>
                      <label className="visually-hidden" htmlFor="new-summary-source">
                        视频链接或分享文本
                      </label>
                      <textarea
                        id="new-summary-source"
                        ref={inputRef}
                        value={sourceText}
                        rows={3}
                        aria-describedby="new-summary-source-hint"
                        placeholder="粘贴视频链接或分享文字（Enter 提交，Shift+Enter 换行）"
                        onChange={(event) => setSourceText(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            event.key !== "Enter" ||
                            event.shiftKey ||
                            event.nativeEvent.isComposing
                          ) {
                            return;
                          }
                          event.preventDefault();
                          event.currentTarget.form?.requestSubmit();
                        }}
                      />
                    </>
                  ) : (
                    <FilePicker
                      id="new-summary-files"
                      accept="video/*,audio/*,.srt,.vtt,.ass,.txt"
                      files={files}
                      limitBytes={null}
                      onChange={setFiles}
                    />
                  )}
                </div>
                <div className="launcher-actions">
                  {sourceMode === "url" ? (
                    <>
                      <span id="new-summary-source-hint" className="visually-hidden">
                        Enter 提交 · Shift+Enter 换行
                      </span>
                      <button
                        className="launcher-mini-button launcher-paste"
                        type="button"
                        aria-label="粘贴剪贴板内容"
                        aria-busy={readingClipboard}
                        disabled={readingClipboard || submitting}
                        onClick={() => void pasteSourceText()}
                      >
                        {readingClipboard ? <SpinnerIcon /> : <ClipboardIcon />}
                      </button>
                    </>
                  ) : null}
                  <button
                    className="launcher-mini-button launcher-submit"
                    type="submit"
                    aria-label={submitting ? "正在处理" : "开始处理"}
                    aria-busy={submitting}
                    disabled={!canSubmit}
                  >
                    {submitting ? <SpinnerIcon /> : <SendIcon />}
                  </button>
                </div>
              </div>
            )}

            {!loading && summarySettings.enabled && !summarySettings.profileId && (
              <InlineNotice tone="danger">请先在默认模型中配置并验证总结模型。</InlineNotice>
            )}
            {error && <InlineNotice tone="danger">{error}</InlineNotice>}
          </form>
      </ModalDialog>

      <SummarySettingsDialog
        open={summarySettingsOpen}
        settings={summarySettings}
        profiles={notesProfiles}
        onClose={() => setSummarySettingsOpen(false)}
        onSave={setSummarySettings}
      />
    </>
  );
}

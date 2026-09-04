import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  BatchSourceProbe,
  DefaultsView,
  ProfileView,
  SourceProbe,
  Task,
} from "../api/types";
import { formatDuration } from "../app/format";
import { loadPreferences } from "../app/preferences";
import { useRouter } from "../app/router";
import {
  ClipboardIcon,
  LinkIcon,
  SendIcon,
  SettingsIcon,
  SpinnerIcon,
  UploadIcon,
} from "../app/icons";
import { FilePicker } from "../components/FilePicker";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import { SelectionToggleButton } from "../components/SelectionToggleButton";
import {
  SegmentedTabs,
  segmentedTabId,
  type SegmentedTabItem,
} from "../components/SegmentedTabs";
import { Skeleton, SkeletonStatus } from "../components/Skeleton";
import {
  SummarySettingsDialog,
} from "../features/task-creation/SummarySettingsDialog";
import { LauncherSignal } from "../features/task-creation/LauncherSignal";
import {
  buildTaskCreationOptions,
  extractSourceUrls,
  isSubtitleFile,
} from "../features/task-creation/model";
import { createUrlTasksInBatches } from "../features/task-creation/createBatchTasks";
import {
  availableNotesProfiles,
  initialSummarySettings,
  type SummaryTaskSettings,
} from "../features/summary-settings/model";
import { useTaskQueue } from "../features/task-queue/TaskQueueProvider";

type SourceMode = "url" | "media";

const CREATE_SOURCE_TABS_ID = "create-source-tabs";
const CREATE_SOURCE_PANEL_ID = "create-source-panel";
const createSourceTabs: readonly SegmentedTabItem<SourceMode>[] = [
  {
    value: "url",
    label: <><LinkIcon />链接</>,
    panelId: CREATE_SOURCE_PANEL_ID,
  },
  {
    value: "media",
    label: <><UploadIcon />上传</>,
    panelId: CREATE_SOURCE_PANEL_ID,
  },
];

const SOURCE_SITE_LABELS: Record<SourceProbe["source_kind"], string> = {
  bilibili: "B站",
  douyin: "抖音",
  youtube: "YouTube",
};

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "unsafe_source_url" && error.message.includes("proxy Fake-IP")) {
      return "检测到代理 Fake-IP。请在代理软件中排除该平台域名后重试。";
    }
    if (error.code === "auth_required") {
      return "该平台仍要求登录或验证 Cookie。请确认已在授权浏览器中登录；Windows Chrome/Edge 无法导入时请改用 Firefox，然后重启 VtNote。";
    }
    if (error.code === "temporary") return "平台暂时不可用，请稍后重试。";
    if (error.code === "removed") return "视频已删除或当前不可访问。";
    if (error.code === "adapter_drift") {
      return "平台页面已发生变化，当前适配器需要更新。";
    }
    if (error.code === "invalid_subtitle") {
      return "字幕文件无有效内容或格式不正确。";
    }
    if (error.code === "unsupported_upload_extension") {
      return "不支持该文件格式。";
    }
    return error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") return "上传已取消";
  return "操作失败，请检查本地服务后重试。";
}

export function CreateTaskPage() {
  const { navigate } = useRouter();
  const {
    consumePendingPaste,
    notify,
    pendingPaste,
    registerTasks,
  } = useTaskQueue();
  const [sourceMode, setSourceMode] = useState<SourceMode>("url");
  const [url, setUrl] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [summarySettingsOpen, setSummarySettingsOpen] = useState(false);
  const [summarySettingsInitialized, setSummarySettingsInitialized] = useState(false);
  const [summarySettings, setSummarySettings] = useState<SummaryTaskSettings>(() => ({
    enabled: loadPreferences().defaultExportItems.includes("notes"),
    profileId: "",
    outputLanguage: "zh-Hans",
  }));
  const [readingClipboard, setReadingClipboard] = useState(false);
  const [collectionProbe, setCollectionProbe] = useState<SourceProbe | null>(null);
  const [batchProbe, setBatchProbe] = useState<BatchSourceProbe | null>(null);
  const [selectedCollectionItems, setSelectedCollectionItems] = useState<Set<string>>(
    new Set(),
  );
  const [selectedBatchSources, setSelectedBatchSources] = useState<Set<string>>(
    new Set(),
  );
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{
    loaded: number;
    total: number;
    current: number;
    count: number;
  } | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const sourceUrlInput = useRef<HTMLTextAreaElement | null>(null);
  const batchProbeSection = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.request<DefaultsView>("/api/defaults", { signal: controller.signal }),
      api.request<ProfileView[]>("/api/profiles", { signal: controller.signal }),
    ])
      .then(([nextDefaults, nextProfiles]) => {
        setDefaults(nextDefaults);
        setProfiles(nextProfiles);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!batchProbe) return;
    batchProbeSection.current?.scrollIntoView?.({ block: "nearest" });
  }, [batchProbe]);

  useEffect(() => {
    if (!defaults || summarySettingsInitialized) return;
    setSummarySettings((current) =>
      initialSummarySettings(defaults, profiles, current.enabled),
    );
    setSummarySettingsInitialized(true);
  }, [defaults, profiles, summarySettingsInitialized]);

  useEffect(() => {
    if (pendingPaste === null) return;
    setSourceMode("url");
    setUrl(pendingPaste);
    setCollectionProbe(null);
    setBatchProbe(null);
    setSelectedCollectionItems(new Set());
    setSelectedBatchSources(new Set());
    setError(null);
    consumePendingPaste();
    window.requestAnimationFrame(() => sourceUrlInput.current?.focus());
  }, [consumePendingPaste, pendingPaste]);

  const notesProfiles = availableNotesProfiles(profiles);
  const sourceUrl = url.trim();
  const sharedLinkCount = extractSourceUrls(sourceUrl).length;
  const sourceReady = sourceMode === "url" ? Boolean(sourceUrl) : files.length > 0;
  const canSubmit =
    sourceReady &&
    !submitting &&
    !loading &&
    !(sourceMode === "url" && Boolean(batchProbe));
  const collection = collectionProbe?.collection;
  const collectionSelectionState =
    selectedCollectionItems.size === 0
      ? "off"
      : collection && selectedCollectionItems.size === collection.items.length
        ? "on"
        : "mixed";
  const batchSelectionState =
    selectedBatchSources.size === 0
      ? "off"
      : batchProbe && selectedBatchSources.size === batchProbe.valid_sources.length
        ? "on"
        : "mixed";
  const launcherSignalPhase = submitting
    ? sourceMode === "url" && !collection && !batchProbe
      ? "probing"
      : "submitting"
    : null;
  const launcherSignalText =
    launcherSignalPhase === "probing"
      ? sharedLinkCount > 1
        ? `正在检查 ${sharedLinkCount} 个链接`
        : "正在检查链接"
      : sourceMode === "media"
        ? uploadProgress
          ? `正在上传 ${uploadProgress.current}/${uploadProgress.count}`
          : "正在上传文件"
        : "正在创建任务";

  const taskOptions = (subtitleUpload = false) => {
    return buildTaskCreationOptions({
      defaults,
      settings: summarySettings,
      selectedOutputs: loadPreferences().defaultExportItems,
      subtitleUpload,
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    setUploadProgress(null);
    try {
      if (sourceMode === "url") {
        const sharedLinks = extractSourceUrls(sourceUrl);
        if (sharedLinks.length > 1) {
          const nextBatch = await api.request<BatchSourceProbe>(
            "/api/sources/probe-batch",
            { method: "POST", body: { text: sourceUrl } },
          );
          setBatchProbe(nextBatch);
          setSelectedBatchSources(
            new Set(nextBatch.valid_sources.map((source) => source.url)),
          );
          notify(
            nextBatch.valid_sources.length > 0
              ? "链接识别完成"
              : "没有可处理的链接",
            nextBatch.valid_sources.length > 0 ? "success" : "muted",
          );
          return;
        }
        const probe = await api.request<SourceProbe>("/api/sources/probe", {
          method: "POST",
          body: { url: sourceUrl },
        });
        if (probe.result_type === "collection" && probe.collection) {
          setCollectionProbe(probe);
          setSelectedCollectionItems(
            new Set(probe.collection.items.map((item) => item.id)),
          );
          notify("链接识别完成");
          return;
        }
        const createdTask = await api.request<Task>("/api/tasks", {
          method: "POST",
          body: {
            sources: [{ kind: "url", locator: probe.canonical_url }],
            ...taskOptions(),
          },
        });
        registerTasks([createdTask]);
      } else {
        const controller = new AbortController();
        uploadController.current = controller;
        const selectedFiles = [...files];
        const totalBytes = Math.max(
          1,
          selectedFiles.reduce((total, selectedFile) => total + selectedFile.size, 0),
        );
        const failedFiles: File[] = [];
        const createdTasks: Task[] = [];
        let completedBytes = 0;
        let completedCount = 0;

        for (const [index, selectedFile] of selectedFiles.entries()) {
          const subtitleUpload = isSubtitleFile(selectedFile);
          try {
            const createdTask = await api.uploadTask<Task>(
              selectedFile,
              {
                kind: subtitleUpload ? "subtitle" : "media",
                ...taskOptions(subtitleUpload),
              },
              {
                signal: controller.signal,
                onProgress: (loaded, total) => {
                  const fileProgress = total > 0 ? loaded / total : 0;
                  setUploadProgress({
                    loaded:
                      completedBytes +
                      Math.round(selectedFile.size * Math.min(1, fileProgress)),
                    total: totalBytes,
                    current: index + 1,
                    count: selectedFiles.length,
                  });
                },
              },
            );
            createdTasks.push(createdTask);
            completedBytes += selectedFile.size;
            completedCount += 1;
          } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") {
              setFiles(selectedFiles.slice(index));
              throw caught;
            }
            failedFiles.push(selectedFile);
          }
        }

        registerTasks(createdTasks);

        if (failedFiles.length > 0) {
          setFiles(failedFiles);
          setUploadProgress(null);
          setError(
            completedCount > 0
              ? `已创建 ${completedCount} 个任务，${failedFiles.length} 个文件失败。`
              : `${failedFiles.length} 个文件上传失败。`,
          );
          return;
        }
      }
      navigate("/tasks", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      uploadController.current = null;
      setSubmitting(false);
    }
  };

  const submitCollection = async () => {
    if (!collection || selectedCollectionItems.size === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const selectedItems = collection.items.filter((item) =>
        selectedCollectionItems.has(item.id),
      );
      const createdTasks = await createUrlTasksInBatches(
        selectedItems.map((item) => item.canonical_url),
        taskOptions(),
      );
      registerTasks(createdTasks);
      navigate("/tasks", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const submitBatch = async () => {
    if (!batchProbe || selectedBatchSources.size === 0 || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const selectedSources = batchProbe.valid_sources.filter((source) =>
        selectedBatchSources.has(source.url),
      );
      const createdTasks = await createUrlTasksInBatches(
        selectedSources.map((source) => source.url),
        taskOptions(),
      );
      registerTasks(createdTasks);
      navigate("/tasks", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const toggleCollectionItem = (id: string) => {
    setSelectedCollectionItems((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllCollectionItems = () => {
    if (!collection) return;
    setSelectedCollectionItems(
      collectionSelectionState === "on"
        ? new Set()
        : new Set(collection.items.map((item) => item.id)),
    );
  };

  const toggleBatchSource = (url: string) => {
    setSelectedBatchSources((current) => {
      const next = new Set(current);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const toggleAllBatchSources = () => {
    if (!batchProbe) return;
    setSelectedBatchSources(
      batchSelectionState === "on"
        ? new Set()
        : new Set(batchProbe.valid_sources.map((source) => source.url)),
    );
  };

  const replaceSourceUrl = (value: string) => {
    setUrl(value);
    setCollectionProbe(null);
    setBatchProbe(null);
    setSelectedCollectionItems(new Set());
    setSelectedBatchSources(new Set());
    setError(null);
  };

  const pasteSourceUrl = async () => {
    setReadingClipboard(true);
    setError(null);
    try {
      const clipboardText = await navigator.clipboard.readText();
      if (!clipboardText) return;
      replaceSourceUrl(clipboardText);
      sourceUrlInput.current?.focus();
    } catch {
      setError("无法读取剪贴板，请检查浏览器剪贴板权限。");
    } finally {
      setReadingClipboard(false);
    }
  };

  const selectSourceMode = (nextMode: SourceMode) => {
    setSourceMode(nextMode);
    if (nextMode === "media") {
      setCollectionProbe(null);
      setBatchProbe(null);
      setSelectedCollectionItems(new Set());
      setSelectedBatchSources(new Set());
    }
    setError(null);
  };

  if (loading) return <CreateTaskSkeleton />;

  return (
    <div className="page launcher-page">
      <header className="launcher-heading">
        <h1>把音视频变成字幕和笔记</h1>
      </header>

      <form className="launcher" onSubmit={submit}>
        <div className="launcher-toolbar">
          <SegmentedTabs
            id={CREATE_SOURCE_TABS_ID}
            className="launcher-tabs"
            ariaLabel="输入方式"
            items={createSourceTabs}
            value={sourceMode}
            onValueChange={selectSourceMode}
          />

          <div className="launcher-tools" aria-label="处理设置">
            <button
              type="button"
              className="launcher-settings-button"
              aria-haspopup="dialog"
              onClick={() => setSummarySettingsOpen(true)}
            >
              <SettingsIcon />
              总结设置
            </button>
          </div>
        </div>

        <div
          id={CREATE_SOURCE_PANEL_ID}
          className="launcher-input"
          data-source-mode={sourceMode}
          role="tabpanel"
          aria-labelledby={segmentedTabId(CREATE_SOURCE_TABS_ID, sourceMode)}
        >
          <div className="launcher-source">
            {sourceMode === "url" ? (
              <>
              <label className="visually-hidden" htmlFor="source-url">
                视频链接或分享文本
              </label>
              <textarea
                id="source-url"
                ref={sourceUrlInput}
                inputMode="url"
                value={url}
                rows={3}
                aria-describedby="source-url-hint"
                placeholder="粘贴视频链接或分享文字"
                onChange={(event) => replaceSourceUrl(event.target.value)}
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
                id="source-file"
                accept=".mp4,.mkv,.mov,.webm,.avi,.m4v,.aac,.mp3,.m4a,.wav,.flac,.ogg,.opus,.srt,.vtt,.ass,.txt"
                files={files}
                limitBytes={null}
                onChange={setFiles}
              />
            )}
          </div>
          <div className="launcher-actions">
            <LauncherSignal
              phase={launcherSignalPhase}
              text={launcherSignalText}
            />
            {sourceMode === "url" && (
              <>
                <span id="source-url-hint" className="visually-hidden">
                  Enter 提交 · Shift+Enter 换行
                </span>
                <button
                  className="launcher-mini-button launcher-paste"
                  type="button"
                  aria-label="粘贴剪贴板内容"
                  aria-busy={readingClipboard}
                  disabled={readingClipboard || submitting}
                  onClick={() => void pasteSourceUrl()}
                >
                  {readingClipboard ? <SpinnerIcon /> : <ClipboardIcon />}
                </button>
              </>
            )}
            <button
              className="launcher-submit"
              type="submit"
              aria-label={submitting ? "正在处理" : "开始处理"}
              aria-busy={submitting}
              disabled={!canSubmit}
            >
              {submitting ? <SpinnerIcon /> : <SendIcon />}
              <span>{submitting ? "正在处理" : "开始处理"}</span>
            </button>
          </div>
        </div>

      </form>

      <MotionPresence present={Boolean(collection)}>
        {collection ? (
        <section className="collection-picker" aria-labelledby="collection-title">
          <header className="collection-picker-heading">
            <div>
              <h2 id="collection-title">
                {SOURCE_SITE_LABELS[collectionProbe.source_kind]}合集
                <span> · </span>
                {collection.title}
              </h2>
              <p>
                共 {collection.total_items} 个视频，已选择 {selectedCollectionItems.size} 个
              </p>
            </div>
            <SelectionToggleButton
              state={collectionSelectionState}
              selectAllLabel="全选视频"
              clearAllLabel="取消全选"
              disabled={submitting}
              onClick={toggleAllCollectionItems}
            />
          </header>

          {collection.truncated && (
            <InlineNotice tone="warning">
              该合集超过 {collection.items.length} 个视频，本次先显示前 {collection.items.length} 个。
            </InlineNotice>
          )}

          <fieldset className="collection-items">
            <legend className="visually-hidden">选择要解析的视频</legend>
            {collection.items.map((item, index) => (
              <label className="collection-item" key={item.id}>
                <input
                  type="checkbox"
                  checked={selectedCollectionItems.has(item.id)}
                  onChange={() => toggleCollectionItem(item.id)}
                />
                <span className="collection-item-index" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="collection-item-title">{item.title}</span>
                <span className="collection-item-duration">
                  {formatDuration(item.duration_ms)}
                </span>
              </label>
            ))}
          </fieldset>

          <footer className="collection-picker-footer">
            <p aria-live="polite">
              {selectedCollectionItems.size > 0
                ? `将创建 ${selectedCollectionItems.size} 个独立处理任务`
                : "请至少选择一个视频"}
            </p>
            <button
              type="button"
              className="button collection-start"
              disabled={selectedCollectionItems.size === 0 || submitting}
              onClick={() => void submitCollection()}
            >
              {submitting
                ? "正在创建任务"
                : `开始解析 ${selectedCollectionItems.size} 个视频`}
            </button>
          </footer>
        </section>
        ) : null}
      </MotionPresence>

      <MotionPresence present={Boolean(batchProbe)}>
        {batchProbe ? (
        <section
          ref={batchProbeSection}
          className="batch-probe"
          aria-labelledby="batch-probe-title"
        >
          <header className="batch-probe-heading">
            <div>
              <p className="batch-probe-eyebrow">已识别批量链接</p>
              <h2 id="batch-probe-title">选择要处理的视频</h2>
              <p>
                共 {batchProbe.results.length} 条，{batchProbe.valid_sources.length} 个可处理，
                已选择 {selectedBatchSources.size} 个
              </p>
            </div>
            <SelectionToggleButton
              state={batchSelectionState}
              selectAllLabel="全选链接"
              clearAllLabel="取消全选"
              disabled={submitting || batchProbe.valid_sources.length === 0}
              onClick={toggleAllBatchSources}
            />
          </header>
          <ol>
            {batchProbe.results.map((result, index) => (
              <li key={`${result.input_url}-${index}`} data-status={result.status}>
                {result.status === "ready" && result.canonical_url ? (
                  <label className="batch-probe-option">
                    <input
                      type="checkbox"
                      checked={selectedBatchSources.has(result.canonical_url)}
                      disabled={submitting}
                      onChange={() => {
                        if (result.canonical_url) toggleBatchSource(result.canonical_url);
                      }}
                    />
                    <span>{result.title || result.input_url}</span>
                  </label>
                ) : (
                  <span>{result.title || result.input_url}</span>
                )}
                <strong>
                  {result.status === "ready"
                      ? "可处理"
                      : result.status === "duplicate"
                        ? "重复"
                      : result.status === "collection_requires_separate_import"
                        ? "请单独导入合集"
                        : "失败"}
                </strong>
              </li>
            ))}
          </ol>
          <footer className="batch-probe-footer">
            <p aria-live="polite">
              {selectedBatchSources.size > 0
                ? `将创建 ${selectedBatchSources.size} 个独立处理任务`
                : "请至少选择一个视频"}
            </p>
            <button
              type="button"
              className="button batch-probe-start"
              disabled={selectedBatchSources.size === 0 || submitting}
              onClick={() => void submitBatch()}
            >
              {submitting
                ? "正在创建任务"
                : `开始处理 ${selectedBatchSources.size} 个视频`}
            </button>
          </footer>
        </section>
        ) : null}
      </MotionPresence>

      <MotionPresence present={Boolean(error)}>
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      </MotionPresence>
      <MotionPresence present={Boolean(uploadProgress)}>
        {uploadProgress ? (
        <div className="upload-progress" aria-live="polite">
          <progress value={uploadProgress.loaded} max={uploadProgress.total} />
          <span>
            正在导入 {uploadProgress.current}/{uploadProgress.count}
          </span>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => uploadController.current?.abort()}
          >
            取消上传
          </button>
        </div>
        ) : null}
      </MotionPresence>

      <SummarySettingsDialog
        open={summarySettingsOpen}
        settings={summarySettings}
        profiles={notesProfiles}
        onClose={() => setSummarySettingsOpen(false)}
        onSave={setSummarySettings}
      />
    </div>
  );
}

function CreateTaskSkeleton() {
  return (
    <div className="page launcher-page">
      <header className="launcher-heading">
        <h1>把音视频变成字幕和笔记</h1>
      </header>
      <SkeletonStatus className="launcher-skeleton" label="正在准备新建处理页面">
        <div className="launcher-skeleton-tabs">
          <Skeleton className="is-block" />
          <Skeleton className="is-block" />
        </div>
        <div className="launcher-skeleton-input">
          <div>
            <Skeleton />
            <Skeleton />
          </div>
          <div className="launcher-skeleton-actions">
            <Skeleton className="is-block" />
            <Skeleton className="is-block" />
          </div>
        </div>
      </SkeletonStatus>
    </div>
  );
}

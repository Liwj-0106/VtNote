import {
  type CSSProperties,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, api } from "../api/client";
import type {
  LibraryOrganization,
  LibraryMetadata,
  NoteResult,
  StageRun,
  TaskItem,
  Transcript,
  TranscriptSegment,
  Translation,
} from "../api/types";
import { formatTimestamp, sourceLabel, statusLabel } from "../app/format";
import { useApiResource, useTaskPolling } from "../app/hooks";
import { AppLink, useRouter } from "../app/router";
import {
  CheckIcon,
  ChevronDownIcon,
  ClipboardIcon,
  FolderPlusIcon,
  RefreshIcon,
  SettingsIcon,
  SparkIcon,
  TasksIcon,
} from "../app/icons";
import { EmptyState } from "../components/EmptyState";
import { DropdownMenu } from "../components/DropdownMenu";
import { ExportMenu } from "../components/ExportMenu";
import { InlineNotice } from "../components/InlineNotice";
import { cleanNoteMarkdown, MarkdownNote } from "../components/MarkdownNote";
import { MotionPresence } from "../components/MotionPresence";
import {
  SegmentedTabs,
  segmentedTabId,
  type SegmentedTabItem,
} from "../components/SegmentedTabs";
import { Skeleton, SkeletonStatus } from "../components/Skeleton";
import { TranscriptViewer } from "../components/TranscriptViewer";
import { OrganizeDialog } from "../features/library-discovery/OrganizeDialog";
import {
  buildOriginalChapters,
  type OriginalChapterRequest,
  OriginalTextView,
  originalToMarkdown,
  originalToText,
} from "../features/task-detail/OriginalTextView";
import { ResultDownloadMenu } from "../features/task-detail/ResultDownloadMenu";
import {
  SourceVideoPanel,
  type VideoSeekRequest,
} from "../features/task-detail/SourceVideoPanel";
import {
  SummarySettingsDialog,
  type SummarySettings,
} from "../features/task-detail/SummarySettingsDialog";
import { useTaskQueue } from "../features/task-queue/TaskQueueProvider";
import {
  groupTranscriptSegments,
  transcriptToJson,
  transcriptToLrc,
  transcriptToSrt,
  transcriptToTimestampedText,
  transcriptToVtt,
} from "../features/transcript-review/transcriptGrouping";

type ResultTab = "notes" | "original" | "transcript" | "translation";
type JsonRecord = Record<string, unknown>;
const DETAIL_SOURCE_RATIO_KEY = "vtnote.detail.sourceRatio";
const DETAIL_SUBTITLE_SCROLL_KEY = "vtnote.detail.subtitleScroll";
const DETAIL_SUBTITLE_GROUP_KEY = "vtnote.detail.subtitleGroupSize";
const RESULT_TABS_ID = "task-result-tabs";
const RESULT_PANEL_ID = "task-result-panel";

function clampSourceRatio(value: number): number {
  return Math.min(0.62, Math.max(0.32, value));
}

function initialSourceRatio(): number {
  const stored = Number(localStorage.getItem(DETAIL_SOURCE_RATIO_KEY));
  return Number.isFinite(stored) && stored > 0
    ? clampSourceRatio(stored)
    : 0.42;
}

function initialSubtitleGroupSize(): number {
  const stored = Number(localStorage.getItem(DETAIL_SUBTITLE_GROUP_KEY));
  return Number.isFinite(stored) && stored >= 1 && stored <= 20
    ? Math.round(stored)
    : 5;
}

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function completedStage(runs: StageRun[], stage: string): boolean {
  return runs.some((run) => run.stage === stage && run.status === "completed");
}

function latestStage(runs: StageRun[], stage: StageRun["stage"]): StageRun | null {
  return runs
    .filter((run) => run.stage === stage)
    .reduce<StageRun | null>(
      (latest, run) => (!latest || run.attempt > latest.attempt ? run : latest),
      null,
    );
}

function translatedSegments(
  transcript: Transcript | null,
  translation: Translation | null,
): TranscriptSegment[] {
  if (!transcript || !translation) return [];
  const texts = new Map(
    translation.entries.map((entry) => [entry.cue_id, entry.text]),
  );
  return transcript.segments.map((segment) => ({
    ...segment,
    text: texts.get(segment.id) ?? segment.text,
  }));
}

export function TaskDetailPage({ taskId }: { taskId: string }) {
  const { path } = useRouter();
  const { notify, registerTasks } = useTaskQueue();
  const { task, error: taskError, refresh } = useTaskPolling(taskId);
  const item = task?.items[0] ?? null;
  const runs = item?.stage_runs ?? [];
  const transcriptReady = completedStage(runs, "transcribe");
  const translationReady = completedStage(runs, "translate");
  const latestNotesRun = latestStage(runs, "notes");
  const notesReady = latestNotesRun?.status === "completed";
  const targetLanguage =
    typeof task?.options.translation_target_language === "string"
      ? task.options.translation_target_language
      : "zh-Hans";
  const transcriptResource = useApiResource<Transcript>(
    item && transcriptReady ? `/api/items/${item.id}/transcript` : null,
  );
  const translationResource = useApiResource<Translation>(
    item && translationReady
      ? `/api/items/${item.id}/translations/${encodeURIComponent(targetLanguage)}`
      : null,
  );
  const notesResource = useApiResource<NoteResult[]>(
    item && notesReady ? `/api/items/${item.id}/notes` : null,
  );
  const organizationResource = useApiResource<LibraryOrganization>(
    task ? `/api/library/tasks/${task.id}` : null,
  );
  const [tab, setTab] = useState<ResultTab>("original");
  const [sourceRatio, setSourceRatio] = useState(initialSourceRatio);
  const [resizing, setResizing] = useState(false);
  const detailLayout = useRef<HTMLDivElement | null>(null);
  const initialTabSelected = useRef(false);
  const [highlightedCue, setHighlightedCue] = useState<string | null>(null);
  const [originalChapterRequest, setOriginalChapterRequest] =
    useState<OriginalChapterRequest | null>(null);
  const [videoSeekRequest, setVideoSeekRequest] =
    useState<VideoSeekRequest | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [retryingNotes, setRetryingNotes] = useState(false);
  const [summarySettingsOpen, setSummarySettingsOpen] = useState(false);
  const [summarySettings, setSummarySettings] =
    useState<SummarySettings | null>(null);
  const [subtitleScrollEnabled, setSubtitleScrollEnabled] = useState(
    () => localStorage.getItem(DETAIL_SUBTITLE_SCROLL_KEY) !== "false",
  );
  const [subtitleGroupSize, setSubtitleGroupSize] = useState(
    initialSubtitleGroupSize,
  );
  const [organizing, setOrganizing] = useState(false);
  const [loadingCollections, setLoadingCollections] = useState(false);
  const [libraryMetadata, setLibraryMetadata] = useState<LibraryMetadata>({
    collections: [],
    tags: [],
  });

  useEffect(() => {
    const query = path.includes("?") ? path.slice(path.indexOf("?") + 1) : "";
    const parameters = new URLSearchParams(query);
    setHighlightedCue(parameters.get("cue"));
    const requestedTab = parameters.get("tab");
    const nextTab = ["notes", "original", "transcript", "translation"].includes(
      requestedTab ?? "",
    )
      ? (requestedTab as ResultTab)
      : null;
    initialTabSelected.current = nextTab !== null;
    setTab(nextTab ?? "original");
  }, [path, taskId]);

  const notesSnapshot = record(task?.pipeline_snapshot.notes);
  const notesProfileSnapshot = record(notesSnapshot?.profile);
  const snapshotNotesProfileId =
    typeof notesProfileSnapshot?.id === "string" ? notesProfileSnapshot.id : "";
  const snapshotNotesModel =
    typeof notesProfileSnapshot?.model === "string"
      ? notesProfileSnapshot.model
      : "默认模型";
  const snapshotOutputLanguage =
    typeof notesSnapshot?.output_language === "string"
      ? notesSnapshot.output_language
      : "zh-Hans";
  const selectedNotesModel = summarySettings?.modelLabel ?? snapshotNotesModel;
  const notesRequested =
    notesSnapshot?.enabled === true ||
    task?.options.notes_enabled === true ||
    task?.options.output_type === "notes" ||
    notesReady;
  const resultTabs = useMemo<readonly SegmentedTabItem<ResultTab>[]>(() => {
    const items: SegmentedTabItem<ResultTab>[] = [];
    if (notesRequested) {
      items.push({ value: "notes", label: "全文总结", panelId: RESULT_PANEL_ID });
    }
    items.push(
      { value: "original", label: "原文", panelId: RESULT_PANEL_ID },
      { value: "transcript", label: "字幕", panelId: RESULT_PANEL_ID },
    );
    return items;
  }, [notesRequested]);

  useEffect(() => {
    if (!task || initialTabSelected.current) return;
    initialTabSelected.current = true;
    setTab(notesRequested ? "notes" : "original");
  }, [notesRequested, task]);

  useEffect(() => {
    if (!translationReady && tab === "translation") setTab("original");
    if (!notesRequested && tab === "notes") setTab("original");
  }, [notesRequested, tab, translationReady]);

  useEffect(() => {
    setSummarySettings(null);
    setSummarySettingsOpen(false);
    setOriginalChapterRequest(null);
    setVideoSeekRequest(null);
  }, [taskId]);

  const translationSegments = useMemo(
    () =>
      translatedSegments(
        transcriptResource.data,
        translationResource.data,
      ),
    [transcriptResource.data, translationResource.data],
  );
  const selectedNote = notesReady ? (notesResource.data?.[0] ?? null) : null;
  const transcript = transcriptResource.data;
  const selectedNoteMarkdown = selectedNote
    ? cleanNoteMarkdown(selectedNote.markdown)
    : null;
  const originalChapters = useMemo(
    () => buildOriginalChapters(transcript?.segments ?? []),
    [transcript?.segments],
  );
  const groupedTranscriptSegments = useMemo(
    () => groupTranscriptSegments(transcript?.segments ?? [], subtitleGroupSize),
    [subtitleGroupSize, transcript?.segments],
  );
  const assignedCollections = organizationResource.data?.collections ?? [];

  const seekVideo = (startMs: number) => {
    setVideoSeekRequest((current) => ({
      startMs,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const copyText = async (text: string, successMessage: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notify(successMessage);
    } catch {
      setActionError("复制失败，请重试。");
    }
  };

  const retryNotes = async () => {
    if (!task || !item || !latestNotesRun || retryingNotes) return;
    setRetryingNotes(true);
    setActionError(null);
    try {
      const retriedItem = await api.request<TaskItem>(`/api/tasks/${task.id}/retry`, {
        method: "POST",
        body: {
          item_id: item.id,
          stage: "notes",
          expected_attempt: latestNotesRun.attempt,
          strategy: "same",
          acknowledge_possible_charge: false,
          ...(summarySettings
            ? {
                notes_profile_id: summarySettings.profileId,
                notes_profile_revision: summarySettings.profileRevision,
                notes_output_language: summarySettings.outputLanguage,
              }
            : {}),
        },
      });
      registerTasks([
        {
          ...task,
          status: retriedItem.status,
          updated_at: retriedItem.updated_at,
          items: task.items.map((current) =>
            current.id === retriedItem.id ? retriedItem : current,
          ),
        },
      ]);
      notesResource.setData(null);
      refresh();
    } catch (caught) {
      setActionError(
        caught instanceof ApiError ? caught.message : "重新生成总结失败。",
      );
    } finally {
      setRetryingNotes(false);
    }
  };

  const openCollections = async () => {
    if (loadingCollections) return;
    setLoadingCollections(true);
    setActionError(null);
    try {
      const metadata = await api.request<LibraryMetadata>("/api/library/meta");
      setLibraryMetadata(metadata);
      setOrganizing(true);
    } catch (caught) {
      setActionError(
        caught instanceof ApiError ? caught.message : "无法读取合集，请重试。",
      );
    } finally {
      setLoadingCollections(false);
    }
  };

  const jumpToOriginalChapter = (chapterId: string) => {
    setOriginalChapterRequest((current) => ({
      id: chapterId,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const jumpToTranscriptChapter = (startMs: number) => {
    const target =
      groupedTranscriptSegments.find(
        (segment) => segment.start_ms <= startMs && segment.end_ms >= startMs,
      ) ??
      groupedTranscriptSegments.find((segment) => segment.start_ms >= startMs);
    if (!target) return;
    document
      .getElementById(target.id)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const downloadText = (
    content: string,
    extension: "json" | "lrc" | "md" | "srt" | "txt" | "vtt",
    successMessage: string,
  ) => {
    if (!item) return;
    const blob = new Blob([content], {
      type: {
        json: "application/json;charset=utf-8",
        lrc: "text/plain;charset=utf-8",
        md: "text/markdown;charset=utf-8",
        srt: "application/x-subrip;charset=utf-8",
        txt: "text/plain;charset=utf-8",
        vtt: "text/vtt;charset=utf-8",
      }[extension],
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const safeTitle = (item.title ?? "VtNote-总结")
      .replace(/[\\/:*?"<>|]+/gu, "-")
      .slice(0, 72);
    anchor.href = url;
    anchor.download = `${safeTitle}.${extension}`;
    anchor.click();
    URL.revokeObjectURL(url);
    notify(successMessage);
  };

  const currentResultText = (): string => {
    if (tab === "notes") return selectedNoteMarkdown ?? "";
    if (!transcript) return "";
    if (tab === "original") return originalToMarkdown(title, transcript.segments);
    if (tab === "transcript") return transcriptToTimestampedText(transcript.segments);
    return "";
  };

  if (!task && !taskError) {
    return <TaskDetailSkeleton />;
  }
  if (!task) {
    return (
      <div className="page">
        <EmptyState
          title="没有找到这个任务"
          description="任务可能不存在，或本地服务暂时无法读取它。"
          actionLabel="返回任务列表"
          actionTo="/tasks"
        />
      </div>
    );
  }
  if (!item) {
    return (
      <div className="page">
        <EmptyState
          title="任务没有可读取的条目"
          description="返回任务列表后重新创建。"
          actionLabel="返回任务列表"
          actionTo="/tasks"
        />
      </div>
    );
  }

  const title =
    item.title ?? item.source_display_name ?? `${sourceLabel(item.source_kind)} 任务`;
  const resizeFromClientX = (clientX: number) => {
    const bounds = detailLayout.current?.getBoundingClientRect();
    if (!bounds || bounds.width <= 0) return;
    setSourceRatio(clampSourceRatio((clientX - bounds.left) / bounds.width));
  };
  const persistSourceRatio = (nextRatio = sourceRatio) => {
    localStorage.setItem(DETAIL_SOURCE_RATIO_KEY, String(nextRatio));
  };

  return (
    <div
      className="page detail-page"
      style={
        { "--detail-source-ratio": `${sourceRatio * 100}%` } as CSSProperties
      }
    >
      <div className="detail-primary-toolbar">
        <div className="detail-settings-toolbar">
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={() => setSummarySettingsOpen(true)}
          >
            <SettingsIcon />
            总结设置
          </button>
          <span className="detail-model-badge" title={selectedNotesModel}>
            <SparkIcon />
            <span>{selectedNotesModel}</span>
          </span>
        </div>
        <span aria-hidden="true" />
        <SegmentedTabs
          id={RESULT_TABS_ID}
          className="result-tabs"
          ariaLabel="处理结果"
          items={resultTabs}
          value={tab}
          onValueChange={setTab}
        />
      </div>

      <MotionPresence present={Boolean(actionError)}>
        {actionError ? <InlineNotice tone="danger">{actionError}</InlineNotice> : null}
      </MotionPresence>

      <div
        ref={detailLayout}
        className={`detail-layout${resizing ? " is-resizing" : ""}`}
      >
        <SourceVideoPanel
          sourceKind={item.source_kind}
          locator={item.source_locator}
          title={title}
          seekRequest={videoSeekRequest}
        />
        <div
          className="detail-resizer"
          role="separator"
          aria-label="调整视频和结果宽度"
          aria-orientation="vertical"
          aria-valuemin={32}
          aria-valuemax={62}
          aria-valuenow={Math.round(sourceRatio * 100)}
          tabIndex={0}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            setResizing(true);
            resizeFromClientX(event.clientX);
          }}
          onPointerMove={(event) => {
            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
            resizeFromClientX(event.clientX);
          }}
          onPointerUp={(event) => {
            if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
            const bounds = detailLayout.current?.getBoundingClientRect();
            const next = bounds
              ? clampSourceRatio((event.clientX - bounds.left) / bounds.width)
              : sourceRatio;
            event.currentTarget.releasePointerCapture(event.pointerId);
            setSourceRatio(next);
            persistSourceRatio(next);
            setResizing(false);
          }}
          onPointerCancel={() => setResizing(false)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const next = clampSourceRatio(
              sourceRatio + (event.key === "ArrowRight" ? 0.02 : -0.02),
            );
            setSourceRatio(next);
            persistSourceRatio(next);
          }}
        />
        <div className="result-column">
        <section className="result-reader" aria-labelledby="result-heading">
          <h2 id="result-heading" className="visually-hidden">
            处理结果
          </h2>
          <div className="result-sticky-header">
          {((tab === "notes" && selectedNote) ||
            ((tab === "original" || tab === "transcript") && transcript)) ? (
            <div className="result-actions-bar result-original-toolbar result-unified-toolbar">
              <div className="result-toolbar-leading">
                {(tab === "original" || tab === "transcript") && (
                  <label className="subtitle-scroll-control">
                    <input
                      type="checkbox"
                      checked={subtitleScrollEnabled}
                      onChange={(event) => {
                        setSubtitleScrollEnabled(event.target.checked);
                        localStorage.setItem(
                          DETAIL_SUBTITLE_SCROLL_KEY,
                          String(event.target.checked),
                        );
                      }}
                    />
                    <span className="subtitle-scroll-switch" aria-hidden="true">
                      <span />
                    </span>
                    <span>字幕滚动</span>
                  </label>
                )}

                {organizationResource.loading ? (
                  <button className="result-tool-button" type="button" disabled>合集</button>
                ) : assignedCollections.length > 0 ? (
                  <div className="result-collection-state">
                    <AppLink
                      className="result-collection-chip"
                      to={`/collections/${encodeURIComponent(assignedCollections[0].id)}`}
                    >
                      {assignedCollections[0].name}
                      {assignedCollections.length > 1 ? ` +${assignedCollections.length - 1}` : ""}
                    </AppLink>
                    <button
                      className="result-collection-manage"
                      type="button"
                      aria-label="管理合集"
                      aria-busy={loadingCollections}
                      disabled={loadingCollections}
                      onClick={() => void openCollections()}
                    >
                      <FolderPlusIcon />
                    </button>
                  </div>
                ) : (
                  <button
                    className="result-tool-button"
                    type="button"
                    aria-busy={loadingCollections}
                    disabled={loadingCollections}
                    onClick={() => void openCollections()}
                  >
                    <FolderPlusIcon />
                    添加合集
                  </button>
                )}

                {transcript && originalChapters.length > 0 ? (
                  <DropdownMenu
                    ariaLabel="章节目录"
                    size="wide"
                    rootClassName="result-chapter-menu"
                    triggerClassName="result-chapter-trigger"
                    popoverClassName="result-chapter-popover"
                    trigger={
                      <>
                        <TasksIcon />
                        章节 <span>({originalChapters.length})</span>
                        <ChevronDownIcon className="dropdown-menu-chevron" />
                      </>
                    }
                  >
                    {(close) => (
                      <>
                        <strong>章节目录</strong>
                        {originalChapters.map((chapter) => (
                          <button
                            type="button"
                            role="menuitem"
                            key={chapter.id}
                            onClick={() => {
                              if (tab === "transcript") {
                                jumpToTranscriptChapter(chapter.startMs);
                              } else if (tab === "original" || tab === "notes") {
                                jumpToOriginalChapter(chapter.id);
                              } else {
                                setTab("original");
                                jumpToOriginalChapter(chapter.id);
                              }
                              close();
                            }}
                          >
                            <time>{formatTimestamp(chapter.startMs)}</time>
                            <span>{chapter.title}</span>
                          </button>
                        ))}
                      </>
                    )}
                  </DropdownMenu>
                ) : null}

                {tab === "transcript" ? (
                  <label className="subtitle-group-control">
                    <span>字幕分组</span>
                    <input
                      type="range"
                      min="1"
                      max="20"
                      step="1"
                      value={subtitleGroupSize}
                      aria-label="字幕分组条数"
                      onChange={(event) => {
                        const next = Number(event.currentTarget.value);
                        setSubtitleGroupSize(next);
                        localStorage.setItem(DETAIL_SUBTITLE_GROUP_KEY, String(next));
                      }}
                    />
                    <output>{subtitleGroupSize} 条</output>
                  </label>
                ) : null}
              </div>

              <div className="result-toolbar-trailing">
                <button
                  className="result-action-button"
                  type="button"
                  onClick={() =>
                    void copyText(
                      currentResultText(),
                      tab === "notes" ? "已复制总结" : tab === "original" ? "已复制原文" : "已复制字幕",
                    )
                  }
                >
                  <ClipboardIcon />
                  复制
                </button>

                {tab === "notes" ? (
                  <ResultDownloadMenu
                    onMarkdown={() => downloadText(selectedNoteMarkdown ?? "", "md", "已下载总结")}
                    onText={() => downloadText(selectedNoteMarkdown ?? "", "txt", "已下载总结")}
                  />
                ) : tab === "original" && transcript ? (
                  <ResultDownloadMenu
                    onMarkdown={() => downloadText(originalToMarkdown(title, transcript.segments), "md", "已下载原文")}
                    onText={() => downloadText(originalToText(transcript.segments), "txt", "已下载原文")}
                  />
                ) : transcript ? (
                  <ResultDownloadMenu
                    options={[
                      { label: "SRT", description: "通用字幕", action: () => downloadText(transcriptToSrt(transcript.segments), "srt", "已下载 SRT 字幕") },
                      { label: "VTT", description: "网页 / 播放器", action: () => downloadText(transcriptToVtt(transcript.segments), "vtt", "已下载 VTT 字幕") },
                      { label: "JSON", description: "结构化数据，含字段时间戳", action: () => downloadText(transcriptToJson(transcript.segments), "json", "已下载 JSON 字幕") },
                      { label: "TXT", description: "时间戳 + 文本", action: () => downloadText(transcriptToTimestampedText(transcript.segments), "txt", "已下载 TXT 字幕") },
                      { label: "LRC", description: "音乐播放器歌词", action: () => downloadText(transcriptToLrc(transcript.segments), "lrc", "已下载 LRC 字幕") },
                    ]}
                  />
                ) : null}
              </div>
            </div>
          ) : (
            <div className="result-actions-bar">
              <span>{tab === "notes" ? "总结" : tab === "original" ? "原文" : tab === "transcript" ? "字幕" : "译文"}</span>
            </div>
          )}
          </div>
          <div
            id={RESULT_PANEL_ID}
            className="result-panel"
            role="tabpanel"
            aria-label={tab === "translation" ? "译文" : undefined}
            aria-labelledby={
              tab === "translation" ? undefined : segmentedTabId(RESULT_TABS_ID, tab)
            }
          >
            {tab === "notes" &&
              (selectedNote ? (
                <>
                  <div className="summary-status-row">
                    <span>
                      <CheckIcon />
                      总结完成
                    </span>
                    <button type="button" disabled={retryingNotes} onClick={() => void retryNotes()}>
                      <RefreshIcon />
                      {retryingNotes ? "重新总结中…" : "重新总结"}
                    </button>
                  </div>
                  <MarkdownNote markdown={selectedNote.markdown} />
                  {transcript ? (
                    <section
                      className="summary-original-section"
                      aria-labelledby="summary-original-heading"
                    >
                      <header className="summary-original-heading">
                        <h2 id="summary-original-heading">阅读全文</h2>
                      </header>
                      <OriginalTextView
                        segments={transcript.segments}
                        chapterRequest={originalChapterRequest}
                        onSeek={seekVideo}
                      />
                    </section>
                  ) : null}
                </>
              ) : (
                <ResultPending
                  ready={notesReady}
                  error={notesResource.error}
                  taskStatus={task.status}
                  outcome="总结"
                  stageRun={latestNotesRun}
                  retrying={retryingNotes}
                  onRetry={
                    latestNotesRun?.external_submission_state ===
                    "submission_unknown"
                      ? undefined
                      : () => void retryNotes()
                  }
                />
              ))}
            {tab === "original" &&
              (transcript ? (
                <OriginalTextView
                  segments={transcript.segments}
                  chapterRequest={originalChapterRequest}
                  onSeek={seekVideo}
                />
              ) : (
                <ResultPending
                  ready={transcriptReady}
                  error={transcriptResource.error}
                  taskStatus={task.status}
                  outcome="原文"
                />
              ))}
            {tab === "transcript" &&
              (transcript ? (
                <TranscriptViewer
                  segments={groupedTranscriptSegments}
                  highlightedCueId={highlightedCue}
                  variant="cards"
                  scrollEnabled={subtitleScrollEnabled}
                  onSeek={seekVideo}
                />
              ) : (
                <ResultPending
                  ready={transcriptReady}
                  error={transcriptResource.error}
                  taskStatus={task.status}
                  outcome="字幕"
                />
              ))}
            {tab === "translation" &&
              (translationResource.data && transcript ? (
                <>
                  <div className="result-variant-tools">
                    <span>译文 · {translationResource.data.language}</span>
                    <ExportMenu
                      itemId={item.id}
                      variant="translation"
                      language={translationResource.data.language}
                    />
                  </div>
                  <TranscriptViewer segments={translationSegments} />
                </>
              ) : (
                <ResultPending
                  ready={translationReady}
                  error={translationResource.error}
                  taskStatus={task.status}
                  outcome="译文"
                />
              ))}
          </div>
        </section>

        </div>
      </div>
      <OrganizeDialog
        open={organizing}
        taskIds={[task.id]}
        metadata={libraryMetadata}
        onMetadata={setLibraryMetadata}
        onClose={() => setOrganizing(false)}
        onApplied={() => {
          void organizationResource.refresh();
          notify("已添加到合集");
        }}
        collectionsOnly
      />
      <SummarySettingsDialog
        open={summarySettingsOpen}
        initialProfileId={summarySettings?.profileId ?? snapshotNotesProfileId}
        initialOutputLanguage={
          summarySettings?.outputLanguage ?? snapshotOutputLanguage
        }
        onClose={() => setSummarySettingsOpen(false)}
        onSave={(settings) => {
          setSummarySettings(settings);
          setSummarySettingsOpen(false);
          notify("总结设置已保存");
        }}
      />
    </div>
  );
}

function ResultPending({
  ready,
  error,
  taskStatus,
  outcome,
  stageRun,
  retrying = false,
  onRetry,
}: {
  ready: boolean;
  error: Error | null;
  taskStatus: string;
  outcome: "总结" | "原文" | "字幕" | "译文";
  stageRun?: StageRun | null;
  retrying?: boolean;
  onRetry?: () => void;
}) {
  if (error) {
    return (
      <InlineNotice tone="danger">
        结果文件暂时无法读取。原始任务状态不会因此改变。
      </InlineNotice>
    );
  }
  if (ready) {
    return <ResultContentSkeleton outcome={outcome} />;
  }
  if (
    outcome === "总结" &&
    (stageRun?.status === "failed" ||
      ["completed", "completed_with_warnings"].includes(taskStatus))
  ) {
    return (
      <div className="result-pending is-summary-failed">
        <h2>总结未生成</h2>
        {onRetry ? (
          <button
            className="button button-primary result-pending-retry"
            type="button"
            onClick={onRetry}
            disabled={retrying}
          >
            {retrying ? "生成中…" : "重新生成总结"}
          </button>
        ) : null}
      </div>
    );
  }
  return (
    <div className="result-pending">
      <h2>{statusLabel(taskStatus)}</h2>
      <p>{outcome}生成后会显示在这里。</p>
    </div>
  );
}

function TaskDetailSkeleton() {
  return (
    <SkeletonStatus className="page detail-page" label="正在读取任务">
      <div className="detail-primary-toolbar">
        <div className="detail-settings-toolbar detail-skeleton-settings">
          <Skeleton className="detail-skeleton-toolbar-button is-block" />
          <Skeleton className="detail-skeleton-toolbar-button is-block" />
        </div>
        <span aria-hidden="true" />
        <div className="detail-skeleton-tabs">
          <Skeleton className="is-block" />
          <Skeleton className="is-block" />
          <Skeleton className="is-block" />
        </div>
      </div>
      <div className="detail-layout detail-skeleton-layout">
        <div className="source-video-column detail-skeleton-source">
          <Skeleton className="detail-skeleton-video is-block" />
          <div className="detail-skeleton-meta">
            <Skeleton className="detail-skeleton-title" />
            <Skeleton className="detail-skeleton-title is-short" />
            <div className="detail-skeleton-byline">
              <Skeleton />
              <Skeleton />
            </div>
            <Skeleton className="detail-skeleton-description" />
          </div>
        </div>
        <div className="detail-resizer" aria-hidden="true" />
        <div className="result-column">
          <div className="detail-skeleton-action-bar">
            <Skeleton />
            <Skeleton />
          </div>
          <ResultContentSkeleton outcome="原文" />
        </div>
      </div>
    </SkeletonStatus>
  );
}

function ResultContentSkeleton({
  outcome,
}: {
  outcome: "总结" | "原文" | "字幕" | "译文";
}) {
  return (
    <SkeletonStatus
      className="result-content-skeleton"
      label={`正在读取${outcome}`}
    >
      <Skeleton className="result-skeleton-heading" />
      <Skeleton className="result-skeleton-rule" />
      <Skeleton className="result-skeleton-line is-long" />
      <Skeleton className="result-skeleton-line" />
      <Skeleton className="result-skeleton-line is-medium" />
      <Skeleton className="result-skeleton-heading is-secondary" />
      <Skeleton className="result-skeleton-rule" />
      <Skeleton className="result-skeleton-line is-long" />
      <Skeleton className="result-skeleton-line is-short" />
    </SkeletonStatus>
  );
}

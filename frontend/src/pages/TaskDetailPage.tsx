import { useEffect, useMemo, useState } from "react";
import { ApiError, api, isTerminalStatus } from "../api/client";
import type {
  NoteResult,
  StageRun,
  Transcript,
  TranscriptSegment,
  Translation,
} from "../api/types";
import {
  formatDate,
  sourceLabel,
  statusLabel,
} from "../app/format";
import { useApiResource, useTaskPolling } from "../app/hooks";
import { AppLink } from "../app/router";
import { EmptyState } from "../components/EmptyState";
import { ExportMenu } from "../components/ExportMenu";
import { InlineNotice } from "../components/InlineNotice";
import { MarkdownNote } from "../components/MarkdownNote";
import { StageTimeline } from "../components/StageTimeline";
import { TranscriptViewer } from "../components/TranscriptViewer";

type ResultTab = "transcript" | "translation" | "notes";
type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function completedStage(runs: StageRun[], stage: string): boolean {
  return runs.some((run) => run.stage === stage && run.status === "completed");
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
  const { task, error: taskError, refresh } = useTaskPolling(taskId);
  const item = task?.items[0] ?? null;
  const runs = item?.stage_runs ?? [];
  const transcriptReady = completedStage(runs, "transcribe");
  const translationReady = completedStage(runs, "translate");
  const notesReady = completedStage(runs, "notes");
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
  const [tab, setTab] = useState<ResultTab>("transcript");
  const [highlightedCue, setHighlightedCue] = useState<string | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [retryRun, setRetryRun] = useState<StageRun | null>(null);
  const [retryStrategy, setRetryStrategy] = useState<
    "same" | "local" | "cloud_confirmed"
  >("same");
  const [chargeAcknowledged, setChargeAcknowledged] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  useEffect(() => {
    if (!translationReady && tab === "translation") setTab("transcript");
    if (!notesReady && tab === "notes") setTab("transcript");
  }, [notesReady, tab, translationReady]);

  const cloudSnapshot = useMemo(() => {
    const asr = record(task?.pipeline_snapshot.asr);
    return record(asr?.profile);
  }, [task]);
  const translationSegments = useMemo(
    () =>
      translatedSegments(
        transcriptResource.data,
        translationResource.data,
      ),
    [transcriptResource.data, translationResource.data],
  );
  const selectedNote = notesResource.data?.[0] ?? null;

  const cancel = async () => {
    if (!task) return;
    setCanceling(true);
    setActionError(null);
    try {
      await api.request(`/api/tasks/${task.id}/cancel`, { method: "POST" });
      refresh();
    } catch (caught) {
      setActionError(
        caught instanceof ApiError ? caught.message : "停止任务失败。",
      );
    } finally {
      setCanceling(false);
    }
  };

  const openRetry = (run: StageRun) => {
    setRetryRun(run);
    setChargeAcknowledged(false);
    setActionError(null);
    setRetryStrategy(
      run.stage === "transcribe" &&
        run.external_submission_state === "submission_unknown"
        ? "local"
        : "same",
    );
  };

  const retry = async () => {
    if (!task || !item || !retryRun) return;
    const unknown = retryRun.external_submission_state === "submission_unknown";
    const needsAcknowledgement =
      unknown &&
      (retryRun.stage !== "transcribe" ||
        retryStrategy === "cloud_confirmed");
    if (needsAcknowledgement && !chargeAcknowledged) return;
    const body: JsonRecord = {
      item_id: item.id,
      stage: retryRun.stage,
      expected_attempt: retryRun.attempt,
      strategy: retryStrategy,
      acknowledge_possible_charge: needsAcknowledgement,
    };
    if (retryStrategy === "cloud_confirmed") {
      body.cloud_profile_id = cloudSnapshot?.id;
      body.connection_revision = cloudSnapshot?.connection_revision;
      body.profile_revision = cloudSnapshot?.profile_revision;
    }
    setActing(true);
    setActionError(null);
    try {
      await api.request(`/api/tasks/${task.id}/retry`, {
        method: "POST",
        body,
      });
      setRetryRun(null);
      refresh();
    } catch (caught) {
      setActionError(
        caught instanceof ApiError ? caught.message : "重试未能创建。",
      );
    } finally {
      setActing(false);
    }
  };

  const downloadExecutionSummary = async () => {
    if (!item) return;
    try {
      const blob = await api.download(
        `/api/items/${item.id}/execution-summary?format=markdown`,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `vtnote-${item.id.slice(0, 8)}-execution.md`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setActionError(
        caught instanceof ApiError ? caught.message : "执行摘要导出失败。",
      );
    }
  };

  if (!task && !taskError) {
    return (
      <div className="page">
        <p className="muted" role="status">
          正在读取任务…
        </p>
      </div>
    );
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
  const unknownRetry =
    retryRun?.external_submission_state === "submission_unknown";
  const needsChargeAck =
    unknownRetry &&
    (retryRun?.stage !== "transcribe" ||
      retryStrategy === "cloud_confirmed");
  const canCloudRetry =
    typeof cloudSnapshot?.id === "string" &&
    typeof cloudSnapshot.connection_revision === "number" &&
    typeof cloudSnapshot.profile_revision === "number";

  return (
    <div className="page detail-page">
      <header className="detail-header">
        <div>
          <AppLink className="back-link" to="/tasks">
            返回任务
          </AppLink>
          <h1>{title}</h1>
          <p>
            {sourceLabel(item.source_kind)} · 创建于 {formatDate(task.created_at)}
          </p>
        </div>
        <div className="detail-actions">
          {transcriptResource.data && <ExportMenu itemId={item.id} />}
          <button
            className="button button-quiet"
            type="button"
            onClick={() => void downloadExecutionSummary()}
          >
            执行摘要
          </button>
        </div>
      </header>

      {taskError && (
        <InlineNotice tone="warning">
          与本地服务的连接暂时中断，页面保留上一次状态并继续尝试。
        </InlineNotice>
      )}
      {task.status === "completed_with_warnings" && (
        <InlineNotice tone="warning">
          原始字幕已完成，但一个可选处理步骤没有完成。字幕仍可阅读和导出。
        </InlineNotice>
      )}
      {actionError && (
        <InlineNotice tone="danger">{actionError}</InlineNotice>
      )}

      <div className="detail-layout">
        <section className="result-reader" aria-labelledby="result-heading">
          <h2 id="result-heading" className="visually-hidden">
            处理结果
          </h2>
          <div className="result-tabs" role="tablist" aria-label="处理结果">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "transcript"}
              onClick={() => setTab("transcript")}
            >
              字幕
            </button>
            {translationReady && (
              <button
                type="button"
                role="tab"
                aria-selected={tab === "translation"}
                onClick={() => setTab("translation")}
              >
                译文
              </button>
            )}
            {notesReady && (
              <button
                type="button"
                role="tab"
                aria-selected={tab === "notes"}
                onClick={() => setTab("notes")}
              >
                AI 笔记
              </button>
            )}
          </div>
          <div className="result-panel" role="tabpanel">
            {tab === "transcript" &&
              (transcriptResource.data ? (
                <TranscriptViewer
                  segments={transcriptResource.data.segments}
                  highlightedCueId={highlightedCue}
                />
              ) : (
                <ResultPending
                  ready={transcriptReady}
                  error={transcriptResource.error}
                  taskStatus={task.status}
                />
              ))}
            {tab === "translation" &&
              (translationResource.data && transcriptResource.data ? (
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
                />
              ))}
            {tab === "notes" &&
              (selectedNote ? (
                <MarkdownNote
                  markdown={selectedNote.markdown}
                  onCitation={(cueId) => {
                    setHighlightedCue(cueId);
                    setTab("transcript");
                  }}
                />
              ) : (
                <ResultPending
                  ready={notesReady}
                  error={notesResource.error}
                  taskStatus={task.status}
                />
              ))}
          </div>
        </section>

        <aside className="stage-inspector" aria-labelledby="stage-heading">
          <div className="inspector-heading">
            <div>
              <p>处理进度</p>
              <h2 id="stage-heading">{statusLabel(task.status)}</h2>
            </div>
          </div>
          <StageTimeline runs={runs} onRetry={openRetry} />

          {retryRun && (
            <div className="retry-panel" aria-live="polite">
              <h3>重试“{retryRun.stage}”</h3>
              {unknownRetry ? (
                <>
                  <p>
                    上一次提交结果未知，云端任务可能已创建并计费。程序不会自动重复提交。
                  </p>
                  {retryRun.stage === "transcribe" && (
                    <fieldset className="retry-strategies">
                      <legend>选择方式</legend>
                      <label>
                        <input
                          type="radio"
                          name="retry-strategy"
                          value="local"
                          checked={retryStrategy === "local"}
                          onChange={() => setRetryStrategy("local")}
                        />
                        改用本地，不再提交云端
                      </label>
                      {canCloudRetry && (
                        <label>
                          <input
                            type="radio"
                            name="retry-strategy"
                            value="cloud_confirmed"
                            checked={retryStrategy === "cloud_confirmed"}
                            onChange={() =>
                              setRetryStrategy("cloud_confirmed")
                            }
                          />
                          再次提交腾讯云
                        </label>
                      )}
                    </fieldset>
                  )}
                  {needsChargeAck && (
                    <label className="rights-check">
                      <input
                        type="checkbox"
                        checked={chargeAcknowledged}
                        onChange={(event) =>
                          setChargeAcknowledged(event.target.checked)
                        }
                      />
                      我了解这次重试可能产生重复费用。
                    </label>
                  )}
                </>
              ) : (
                <>
                  <p>只会重试这个阶段，不会重新下载或重复完成的阶段。</p>
                  {retryRun.stage === "transcribe" && (
                    <fieldset className="retry-strategies">
                      <legend>选择方式</legend>
                      <label>
                        <input
                          type="radio"
                          name="retry-strategy"
                          value="same"
                          checked={retryStrategy === "same"}
                          onChange={() => setRetryStrategy("same")}
                        />
                        按原配置重试（云端不可用时自动转本地）
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="retry-strategy"
                          value="local"
                          checked={retryStrategy === "local"}
                          onChange={() => setRetryStrategy("local")}
                        />
                        仅使用本地识别模型
                      </label>
                    </fieldset>
                  )}
                </>
              )}
              <div className="actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => setRetryRun(null)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={acting || (needsChargeAck && !chargeAcknowledged)}
                  onClick={() => void retry()}
                >
                  {acting ? "正在创建重试…" : "确认重试"}
                </button>
              </div>
            </div>
          )}

          {!isTerminalStatus(task.status) && (
            <button
              type="button"
              className="button button-quiet cancel-task"
              disabled={canceling}
              onClick={() => void cancel()}
            >
              {canceling ? "正在停止…" : "停止任务"}
            </button>
          )}
        </aside>
      </div>
    </div>
  );
}

function ResultPending({
  ready,
  error,
  taskStatus,
}: {
  ready: boolean;
  error: Error | null;
  taskStatus: string;
}) {
  if (error) {
    return (
      <InlineNotice tone="danger">
        结果文件暂时无法读取。原始任务状态不会因此改变。
      </InlineNotice>
    );
  }
  return (
    <div className="result-pending">
      <h2>{ready ? "正在读取结果" : statusLabel(taskStatus)}</h2>
      <p>
        {ready
          ? "结果已生成，正在从本地存储读取。"
          : "字幕生成后会自动显示在这里。"}
      </p>
    </div>
  );
}

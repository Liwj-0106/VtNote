import { ApiError, isTerminalStatus } from "../../api/client";
import type { StageRun, Task } from "../../api/types";
import { statusLabel } from "../../app/format";

const stageOrder: StageRun["stage"][] = [
  "source",
  "transcribe",
  "translate",
  "notes",
];
const finishedStageStatuses = new Set(["completed", "skipped"]);

const libraryStageLabels: Record<StageRun["stage"], string> = {
  source: "获取来源",
  transcribe: "生成字幕",
  translate: "翻译字幕",
  notes: "生成总结",
};

const failedStageLabels: Record<StageRun["stage"], string> = {
  source: "来源失败",
  transcribe: "识别失败",
  translate: "翻译失败",
  notes: "总结失败",
};

export interface ProgressView {
  label: string;
  ratio: number;
  determinate: boolean;
  valueText: string;
}

export interface TaskRetryPlan {
  itemId: string;
  stage: StageRun["stage"];
  expectedAttempt: number;
  body: Record<string, unknown>;
  description: string;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function taskTitle(task: Task): string {
  const item = task.items[0];
  return item?.title ?? item?.source_display_name ?? "未命名记录";
}

export function latestStageRuns(runs: StageRun[]): StageRun[] {
  const latest = new Map<StageRun["stage"], StageRun>();
  for (const run of runs) {
    const current = latest.get(run.stage);
    if (!current || run.attempt > current.attempt) latest.set(run.stage, run);
  }
  return stageOrder.flatMap((stage) => {
    const run = latest.get(stage);
    return run ? [run] : [];
  });
}

export function taskRetryPlan(task: Task): TaskRetryPlan | null {
  const item = task.items[0];
  if (!item) return null;
  const run = latestStageRuns(item.stage_runs).find((candidate) =>
    ["failed", "canceled"].includes(candidate.status),
  );
  if (!run) return null;

  const asr = record(task.pipeline_snapshot.asr);
  const cloudProfile = record(asr?.profile);
  const cloudProfileId = cloudProfile?.id;
  const connectionRevision = cloudProfile?.connection_revision;
  const profileRevision = cloudProfile?.profile_revision;
  const usesCloudAsr =
    typeof cloudProfileId === "string" &&
    typeof connectionRevision === "number" &&
    typeof profileRevision === "number";
  const submissionUnknown =
    run.external_submission_state === "submission_unknown";
  const body: Record<string, unknown> = {
    item_id: item.id,
    stage: run.stage,
    expected_attempt: run.attempt,
    strategy: "same",
    acknowledge_possible_charge: false,
  };

  if (run.stage === "transcribe" && submissionUnknown) {
    if (!usesCloudAsr) return null;
    body.strategy = "cloud_confirmed";
    body.cloud_profile_id = cloudProfileId;
    body.connection_revision = connectionRevision;
    body.profile_revision = profileRevision;
    body.acknowledge_possible_charge = true;
  } else if (
    submissionUnknown &&
    ["translate", "notes"].includes(run.stage)
  ) {
    body.acknowledge_possible_charge = true;
  }

  let description = "将从失败阶段继续处理。";
  if (run.stage === "source") {
    description = usesCloudAsr
      ? "将重新获取视频来源；成功后继续使用当前腾讯 ASR 设置。"
      : "将重新获取视频来源，并从失败处继续处理。";
  } else if (run.stage === "transcribe" && usesCloudAsr) {
    description = submissionUnknown
      ? "腾讯 ASR 的上次提交结果未知；重新提交可能再次产生费用。"
      : "将重新提交腾讯 ASR，可能再次产生费用。";
  } else if (submissionUnknown) {
    description = "上次云端提交结果未知；重试可能再次产生费用。";
  }

  return {
    itemId: item.id,
    stage: run.stage,
    expectedAttempt: run.attempt,
    body,
    description,
  };
}

function notesRequested(task: Task): boolean {
  const notesSnapshot = task.pipeline_snapshot.notes;
  return (
    (typeof notesSnapshot === "object" &&
      notesSnapshot !== null &&
      "enabled" in notesSnapshot &&
      notesSnapshot.enabled === true) ||
    task.options.notes_enabled === true ||
    task.options.output_type === "notes"
  );
}

export function hasFailedSummary(task: Task): boolean {
  if (!["completed", "completed_with_warnings"].includes(task.status)) return false;
  const notesRun = latestStageRuns(task.items[0]?.stage_runs ?? []).find(
    (run) => run.stage === "notes",
  );
  return (
    notesRun?.status !== "completed" &&
    (notesRequested(task) || ["failed", "canceled"].includes(notesRun?.status ?? ""))
  );
}

export function taskProgress(task: Task): ProgressView | null {
  if (isTerminalStatus(task.status)) return null;
  const runs = latestStageRuns(task.items[0]?.stage_runs ?? []);
  if (runs.length === 0) {
    return {
      label: task.status === "cancel_requested" ? "正在停止" : "等待处理",
      ratio: 0,
      determinate: false,
      valueText: statusLabel(task.status),
    };
  }

  const completed = runs.filter((run) => finishedStageStatuses.has(run.status)).length;
  const current =
    runs.find((run) =>
      ["running", "waiting_external", "cancel_requested"].includes(run.status),
    ) ?? runs.find((run) => !finishedStageStatuses.has(run.status));
  const total = runs.length;
  let ratio = completed / total;
  let determinate = false;

  if (
    current?.progress &&
    typeof current.progress.total === "number" &&
    current.progress.total > 0
  ) {
    const stageRatio = Math.min(
      1,
      Math.max(0, current.progress.current / current.progress.total),
    );
    ratio = Math.min(1, (completed + stageRatio) / total);
    determinate = true;
  }

  const baseLabel = current ? libraryStageLabels[current.stage] : "整理结果";
  const label =
    task.status === "cancel_requested"
      ? "正在停止"
      : current?.status === "waiting_external"
        ? `${baseLabel} · 等待云端`
        : baseLabel;
  return {
    label,
    ratio,
    determinate,
    valueText: determinate
      ? `${label}，${Math.round(ratio * 100)}%`
      : `${label}，已完成 ${completed} / ${total} 个阶段`,
  };
}

export function mergeNewestTasks(current: Task[], incoming: Task[]): Task[] {
  const incomingIds = new Set(incoming.map((task) => task.id));
  return [...incoming, ...current.filter((task) => !incomingIds.has(task.id))];
}

export function terminalLabel(status: string): string {
  if (["completed", "completed_with_warnings"].includes(status)) return "完成";
  if (["failed", "canceled"].includes(status)) return "失败";
  return statusLabel(status);
}

export function taskFailureLabel(task: Task): string | null {
  if (hasFailedSummary(task)) return failedStageLabels.notes;

  const failedStages = new Set(
    task.items.flatMap((item) =>
      latestStageRuns(item.stage_runs)
        .filter((run) => ["failed", "canceled"].includes(run.status))
        .map((run) => run.stage),
    ),
  );
  if (failedStages.size !== 1) return null;
  const [stage] = failedStages;
  return stage ? failedStageLabels[stage] : null;
}

export function taskTerminalLabel(task: Task): string {
  return taskFailureLabel(task) ?? terminalLabel(task.status);
}

export function hasExportableOutcome(task: Task): boolean {
  const item = task.items[0];
  if (!item || !isTerminalStatus(task.status)) return false;
  const successfulStages = new Set(
    latestStageRuns(item.stage_runs)
      .filter((run) => finishedStageStatuses.has(run.status))
      .map((run) => run.stage),
  );
  return (
    (task.pipeline_snapshot.audio_export_enabled === true &&
      successfulStages.has("source")) ||
    successfulStages.has("transcribe") ||
    successfulStages.has("notes")
  );
}

export function deleteErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.code === "task_not_terminal") {
      return "任务仍在处理中，完成或停止后才能删除。";
    }
    if (caught.code === "task_remote_cleanup_pending") {
      return "云端临时文件仍在清理，请稍后重试。";
    }
    if (caught.code === "task_delete_database_busy") {
      return "内容库正忙，请稍后重试。";
    }
  }
  return "删除失败，请稍后重试。";
}

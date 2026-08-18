import type { StageRun } from "../api/types";
import { formatDate, stageLabel, statusLabel } from "../app/format";

const diagnosticLabels: Record<string, string> = {
  model_not_installed: "本地识别模型尚未安装。请先在设置中安装模型，然后重试。",
  local_asr_cuda_unavailable: "本地识别没有检测到可用的 CUDA 显卡。",
  local_asr_runtime_unavailable: "本地识别运行环境暂时不可用。",
  local_asr_model_load_failed: "本地识别模型加载失败。",
  cloud_cos_unavailable: "云端音频超过内联限制，且当前没有可用的 COS。",
  cloud_duration_exceeded: "音频时长超过了云识别限制。",
  cloud_payload_exceeded: "音频大小超过了云识别限制。",
  cloud_profile_unavailable: "云识别配置不可用，已自动尝试本地识别。",
  cloud_server_error: "云识别服务失败，已自动尝试本地识别。",
  cloud_rate_limited: "云识别请求受限，已自动尝试本地识别。",
};

function diagnosticLabel(value: string): string {
  return diagnosticLabels[value] ?? value;
}

function latestRuns(runs: StageRun[]): StageRun[] {
  const latest = new Map<string, StageRun>();
  for (const run of runs) {
    const current = latest.get(run.stage);
    if (!current || current.attempt < run.attempt) latest.set(run.stage, run);
  }
  return [...latest.values()].sort(
    (left, right) =>
      ["source", "transcribe", "translate", "notes"].indexOf(left.stage) -
      ["source", "transcribe", "translate", "notes"].indexOf(right.stage),
  );
}

export function StageTimeline({
  runs,
  onRetry,
}: {
  runs: StageRun[];
  onRetry?: (run: StageRun) => void;
}) {
  return (
    <ol className="stage-timeline">
      {latestRuns(runs).map((run) => {
        const canRetry = ["failed", "canceled"].includes(run.status);
        const history = runs.filter(
          (candidate) =>
            candidate.stage === run.stage && candidate.id !== run.id,
        );
        return (
          <li key={run.id} className={`stage-item stage-${run.status}`}>
            <span className="stage-node" aria-hidden="true" />
            <div className="stage-copy">
              <div className="stage-title-row">
                <strong>{stageLabel(run.stage)}</strong>
                <span>{statusLabel(run.status)}</span>
              </div>
              <p>
                第 {run.attempt} 次
                {run.finished_at ? ` · ${formatDate(run.finished_at)}` : ""}
              </p>
              {run.progress && (
                <div className="stage-progress">
                  {run.progress.total ? (
                    <progress
                      value={run.progress.current}
                      max={run.progress.total}
                    />
                  ) : (
                    <span className="indeterminate-progress" aria-hidden="true" />
                  )}
                  <span>{run.progress.message_code}</span>
                </div>
              )}
              {run.warning && (
                <p className="stage-warning">
                  上游原因：{diagnosticLabel(run.warning)}
                </p>
              )}
              {run.error_code && (
                <p className="stage-error">
                  {diagnosticLabel(run.error_message ?? run.error_code)}
                  <code>{run.error_code}</code>
                </p>
              )}
              {canRetry && onRetry && (
                <button
                  type="button"
                  className="button button-quiet stage-action"
                  onClick={() => onRetry(run)}
                >
                  处理此阶段
                </button>
              )}
              {history.length > 0 && (
                <details className="attempt-history">
                  <summary>查看之前 {history.length} 次尝试</summary>
                  <ul>
                    {history.map((attempt) => (
                      <li key={attempt.id}>
                        第 {attempt.attempt} 次 · {statusLabel(attempt.status)}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

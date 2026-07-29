import type { StageRun } from "../api/types";
import { formatDate, stageLabel, statusLabel } from "../app/format";

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
                <p className="stage-warning">提醒：{run.warning}</p>
              )}
              {run.error_code && (
                <p className="stage-error">
                  {run.error_message ?? "此阶段未完成。"}
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

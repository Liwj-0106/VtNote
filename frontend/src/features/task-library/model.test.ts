import { describe, expect, it } from "vitest";
import type { StageRun, Task } from "../../api/types";
import { taskFailureLabel, taskTerminalLabel } from "./model";

function stageRun(
  stage: StageRun["stage"],
  status = "failed",
  attempt = 1,
): StageRun {
  return {
    id: `${stage}-${attempt}`,
    stage,
    attempt,
    status,
    error_code: status === "failed" ? `${stage}_failed` : null,
    error_message: null,
    warning: null,
    progress: null,
    execution_evidence: null,
    provider_status_code: null,
    external_submission_state: null,
    started_at: "2026-08-30T08:00:00Z",
    finished_at: status === "failed" ? "2026-08-30T08:01:00Z" : null,
    created_at: "2026-08-30T08:00:00Z",
    updated_at: "2026-08-30T08:01:00Z",
  };
}

function failedTask(runs: StageRun[]): Task {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    status: "failed",
    options: {},
    pipeline_snapshot: {},
    terminal_reason_code: null,
    created_at: "2026-08-30T08:00:00Z",
    updated_at: "2026-08-30T08:01:00Z",
    items: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        position: 0,
        source_kind: "url",
        source_locator: "https://example.invalid/video",
        source_display_name: "测试视频",
        status: "failed",
        title: "测试视频",
        stage_runs: runs,
        created_at: "2026-08-30T08:00:00Z",
        updated_at: "2026-08-30T08:01:00Z",
      },
    ],
  };
}

describe("taskFailureLabel", () => {
  it.each([
    ["source", "来源失败"],
    ["transcribe", "识别失败"],
    ["translate", "翻译失败"],
    ["notes", "总结失败"],
  ] as const)("maps a %s failure", (stage, label) => {
    const task = failedTask([stageRun(stage)]);

    expect(taskFailureLabel(task)).toBe(label);
    expect(taskTerminalLabel(task)).toBe(label);
  });

  it("ignores a failed attempt after that stage has been retried", () => {
    const task = failedTask([
      stageRun("transcribe"),
      stageRun("transcribe", "queued", 2),
    ]);

    expect(taskFailureLabel(task)).toBeNull();
    expect(taskTerminalLabel(task)).toBe("失败");
  });

  it("falls back when the failed stage cannot be determined", () => {
    const task = failedTask([stageRun("source", "completed")]);

    expect(taskFailureLabel(task)).toBeNull();
    expect(taskTerminalLabel(task)).toBe("失败");
  });
});

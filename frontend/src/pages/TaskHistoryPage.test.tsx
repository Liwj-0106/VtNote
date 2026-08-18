import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { RouterProvider } from "../app/router";
import { TaskHistoryPage } from "./TaskHistoryPage";

function renderPage() {
  return render(
    <RouterProvider initialPath="/tasks">
      <TaskHistoryPage />
    </RouterProvider>,
  );
}

describe("TaskHistoryPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders one compact row and opens export choices from its export button", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [
        {
          id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          status: "completed",
          options: {},
          pipeline_snapshot: { audio_export_enabled: true },
          terminal_reason_code: null,
          created_at: "2026-08-10T08:00:00Z",
          updated_at: "2026-08-10T08:00:00Z",
          items: [
            {
              id: "11111111-1111-4111-8111-111111111111",
              position: 0,
              source_kind: "bilibili",
              source_locator: "https://www.bilibili.com/video/BV1x",
              source_display_name: "课程视频",
              title: "课程视频",
              status: "completed",
              stage_runs: [
                {
                  id: "source-completed",
                  stage: "source",
                  attempt: 1,
                  status: "completed",
                  error_code: null,
                  error_message: null,
                  warning: null,
                  progress: null,
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: "2026-08-10T08:00:00Z",
                  finished_at: "2026-08-10T08:00:10Z",
                  created_at: "2026-08-10T08:00:00Z",
                  updated_at: "2026-08-10T08:00:10Z",
                },
              ],
              created_at: "2026-08-10T08:00:00Z",
              updated_at: "2026-08-10T08:00:00Z",
            },
          ],
        },
      ],
      nextCursor: null,
    });
    vi.spyOn(api, "request").mockResolvedValue({
      audio: true,
      transcript: false,
      notes: false,
    });

    renderPage();
    expect(await screen.findByRole("heading", { name: "课程视频" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /课程视频/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看详情" })).toHaveAttribute(
      "href",
      "/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    );
    await userEvent.click(screen.getByRole("button", { name: "导出" }));
    expect(await screen.findByRole("dialog", { name: "导出当前结果" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "音频" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "字幕原文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出所选（1）" })).toBeEnabled();
  });

  it("shows the current stage in one unobtrusive progress bar", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [
        {
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          status: "running",
          options: {},
          pipeline_snapshot: {},
          terminal_reason_code: null,
          created_at: "2026-08-13T01:23:00Z",
          updated_at: "2026-08-13T01:23:00Z",
          items: [
            {
              id: "22222222-2222-4222-8222-222222222222",
              position: 0,
              source_kind: "bilibili",
              source_locator: "https://www.bilibili.com/video/BV2x",
              source_display_name: "AI 产品经理",
              title: "我如何从 0 到 1 成为 AI 产品经理？",
              status: "running",
              stage_runs: [
                {
                  id: "source-run",
                  stage: "source",
                  attempt: 1,
                  status: "completed",
                  error_code: null,
                  error_message: null,
                  warning: null,
                  progress: null,
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: "2026-08-13T01:23:00Z",
                  finished_at: "2026-08-13T01:23:10Z",
                  created_at: "2026-08-13T01:23:00Z",
                  updated_at: "2026-08-13T01:23:10Z",
                },
                {
                  id: "transcribe-run",
                  stage: "transcribe",
                  attempt: 1,
                  status: "running",
                  error_code: null,
                  error_message: null,
                  warning: null,
                  progress: {
                    current: 25,
                    total: 100,
                    unit: "items",
                    message_code: "transcribing_segments",
                  },
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: "2026-08-13T01:23:10Z",
                  finished_at: null,
                  created_at: "2026-08-13T01:23:00Z",
                  updated_at: "2026-08-13T01:23:20Z",
                },
                {
                  id: "notes-run",
                  stage: "notes",
                  attempt: 1,
                  status: "queued",
                  error_code: null,
                  error_message: null,
                  warning: null,
                  progress: null,
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: null,
                  finished_at: null,
                  created_at: "2026-08-13T01:23:00Z",
                  updated_at: "2026-08-13T01:23:00Z",
                },
              ],
              created_at: "2026-08-13T01:23:00Z",
              updated_at: "2026-08-13T01:23:20Z",
            },
          ],
        },
      ],
      nextCursor: null,
    });

    renderPage();

    expect(await screen.findByText("生成字幕")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", {
      name: "我如何从 0 到 1 成为 AI 产品经理？处理进度",
    });
    expect(progress).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    expect(screen.queryByText("处理中")).not.toBeInTheDocument();
  });

  it("allows a failed task to export outcomes that were already produced", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [
        {
          id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          status: "failed",
          options: {},
          pipeline_snapshot: { audio_export_enabled: true },
          terminal_reason_code: null,
          created_at: "2026-08-18T15:51:43Z",
          updated_at: "2026-08-18T15:52:11Z",
          items: [
            {
              id: "33333333-3333-4333-8333-333333333333",
              position: 0,
              source_kind: "url",
              source_locator: "https://www.bilibili.com/video/BV3x",
              source_display_name: null,
              title: "识别失败但音频已就绪",
              status: "failed",
              stage_runs: [
                {
                  id: "failed-source-completed",
                  stage: "source",
                  attempt: 1,
                  status: "completed",
                  error_code: null,
                  error_message: null,
                  warning: null,
                  progress: null,
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: "2026-08-18T15:51:43Z",
                  finished_at: "2026-08-18T15:51:53Z",
                  created_at: "2026-08-18T15:51:43Z",
                  updated_at: "2026-08-18T15:51:53Z",
                },
              ],
              created_at: "2026-08-18T15:51:43Z",
              updated_at: "2026-08-18T15:52:11Z",
            },
          ],
        },
      ],
      nextCursor: null,
    });
    vi.spyOn(api, "request").mockResolvedValue({
      audio: true,
      transcript: false,
      notes: false,
    });

    renderPage();
    expect(await screen.findByText("需要处理")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "继续处理" })).toHaveAttribute(
      "href",
      "/tasks/cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    );
    const exportButton = screen.getByRole("button", { name: "导出" });
    expect(exportButton).toBeEnabled();
    await userEvent.click(exportButton);
    expect(await screen.findByRole("checkbox", { name: "音频" })).toBeEnabled();
  });

  it("keeps export disabled but exposes retry when no output was produced", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [
        {
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          status: "failed",
          options: { output_type: "transcript" },
          pipeline_snapshot: {
            output_type: "transcript",
            audio_export_enabled: false,
          },
          terminal_reason_code: null,
          created_at: "2026-08-19T00:37:00Z",
          updated_at: "2026-08-19T00:38:00Z",
          items: [
            {
              id: "44444444-4444-4444-8444-444444444444",
              position: 0,
              source_kind: "uploaded_media",
              source_locator: "asset-id",
              source_display_name: "sample.aac",
              title: "云端失败且本地模型未安装",
              status: "failed",
              stage_runs: [
                {
                  id: "failed-transcribe",
                  stage: "transcribe",
                  attempt: 1,
                  status: "failed",
                  error_code: "model_not_installed",
                  error_message: "model_not_installed",
                  warning: "cloud_cos_unavailable",
                  progress: null,
                  execution_evidence: null,
                  provider_status_code: null,
                  external_submission_state: null,
                  started_at: "2026-08-19T00:37:10Z",
                  finished_at: "2026-08-19T00:38:00Z",
                  created_at: "2026-08-19T00:37:00Z",
                  updated_at: "2026-08-19T00:38:00Z",
                },
              ],
              created_at: "2026-08-19T00:37:00Z",
              updated_at: "2026-08-19T00:38:00Z",
            },
          ],
        },
      ],
      nextCursor: null,
    });

    renderPage();

    expect(await screen.findByText("需要处理")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "继续处理" })).toHaveAttribute(
      "href",
      "/tasks/dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    );
  });
});

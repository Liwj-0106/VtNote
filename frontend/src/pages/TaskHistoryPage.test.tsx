import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Task } from "../api/types";
import { RouterProvider } from "../app/router";
import { TaskHistoryPage } from "./TaskHistoryPage";

function renderPage(path = "/tasks") {
  return render(
    <RouterProvider initialPath={path}>
      <TaskHistoryPage />
    </RouterProvider>,
  );
}

function completedTask(id: string, itemId: string, title: string): Task {
  return {
    id,
    status: "completed",
    options: {},
    pipeline_snapshot: { audio_export_enabled: true },
    terminal_reason_code: null,
    created_at: "2026-08-23T08:00:00Z",
    updated_at: "2026-08-23T08:00:00Z",
    items: [
      {
        id: itemId,
        position: 0,
        source_kind: "youtube",
        source_locator: `https://youtu.be/${id}`,
        source_display_name: title,
        title,
        status: "completed",
        stage_runs: [
          {
            id: `${id}-source`,
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
            started_at: "2026-08-23T08:00:00Z",
            finished_at: "2026-08-23T08:00:10Z",
            created_at: "2026-08-23T08:00:00Z",
            updated_at: "2026-08-23T08:00:10Z",
          },
        ],
        created_at: "2026-08-23T08:00:00Z",
        updated_at: "2026-08-23T08:00:10Z",
      },
    ],
  };
}

function failedSummaryTask(): Task {
  const result = completedTask(
    "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "33333333-3333-4333-8333-333333333333",
    "总结失败内容",
  );
  result.status = "completed_with_warnings";
  result.options = { output_type: "notes", notes_enabled: true };
  result.pipeline_snapshot = { notes: { enabled: true } };
  result.items[0].status = "completed_with_warnings";
  result.items[0].stage_runs.push(
    {
      ...result.items[0].stage_runs[0],
      id: "summary-transcript",
      stage: "transcribe",
    },
    {
      ...result.items[0].stage_runs[0],
      id: "summary-failed",
      stage: "notes",
      status: "failed",
      error_code: "chat_output_truncated",
    },
  );
  return result;
}

describe("TaskHistoryPage", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the table workspace and opens row export choices from its action menu", async () => {
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
    expect(await screen.findByRole("heading", { name: "总结记录" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "课程视频" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "课程视频" })).toHaveAttribute(
      "href",
      "/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    );
    await userEvent.click(screen.getByRole("button", { name: "课程视频操作菜单" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "导出" }));
    expect(await screen.findByRole("dialog", { name: "导出当前结果" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "音频" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "字幕原文" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "导出所选（1）" })).toBeEnabled();
  });

  it("loads public video metadata without showing redundant source and completion labels", async () => {
    const task = completedTask(
      "12121212-1212-4212-8212-121212121212",
      "34343434-3434-4434-8434-343434343434",
      "公开视频标题",
    );
    task.items[0].source_kind = "url";
    task.items[0].source_locator = "https://www.bilibili.com/video/BV1xx411c7mD";
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [task], nextCursor: null });
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/library/meta") return { collections: [], tags: [] } as never;
      if (path === "/api/sources/probe") {
        return {
          result_type: "single",
          source_kind: "bilibili",
          canonical_url: task.items[0].source_locator,
          title: task.items[0].title,
          duration_ms: 720_000,
          author: "公开视频博主",
          published_at: "2024-01-01",
          thumbnail_url: "https://i0.hdslb.com/bfs/archive/public-cover.jpg",
          subtitle_tracks: [],
        } as never;
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    renderPage();

    expect(await screen.findByText("公开视频博主")).toBeInTheDocument();
    expect(screen.getByText("2024-01-01")).toBeInTheDocument();
    expect(screen.getByText("总结时间")).toBeInTheDocument();
    expect(screen.queryByText("音视频")).not.toBeInTheDocument();
    expect(screen.queryByText("完成")).not.toBeInTheDocument();
    expect(document.querySelector<HTMLImageElement>(".library-cover img")?.src).toBe(
      `http://localhost:3000/api/sources/thumbnail?url=${encodeURIComponent(
        task.items[0].source_locator,
      )}`,
    );
    expect(request).toHaveBeenCalledWith(
      "/api/sources/probe",
      expect.objectContaining({
        method: "POST",
        body: { url: task.items[0].source_locator },
      }),
    );
  });

  it("uses row-shaped skeletons while the library is loading", () => {
    vi.spyOn(api, "requestPage").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(api, "request").mockResolvedValue({ collections: [], tags: [] });

    renderPage();

    const loading = screen.getByRole("status", { name: "正在读取总结记录" });
    expect(loading.querySelectorAll(".library-skeleton-cover")).toHaveLength(4);
    expect(loading.querySelectorAll(".library-skeleton-times")).toHaveLength(4);
  });

  it("labels a terminal task without a requested summary as summary failed", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [failedSummaryTask()],
      nextCursor: null,
    });
    vi.spyOn(api, "request").mockResolvedValue({ collections: [], tags: [] });

    renderPage();

    expect(await screen.findByText("总结失败")).toBeInTheDocument();
    expect(screen.queryByText("完成")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "总结失败内容" })).toHaveAttribute(
      "href",
      "/tasks/cccccccc-cccc-4ccc-8ccc-cccccccccccc?tab=notes",
    );
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

    expect(await screen.findByText("处理中")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", {
      name: "我如何从 0 到 1 成为 AI 产品经理？处理进度",
    });
    expect(progress).toHaveAttribute("aria-valuenow", "42");
    await userEvent.click(
      screen.getByRole("button", { name: "我如何从 0 到 1 成为 AI 产品经理？操作菜单" }),
    );
    expect(screen.getByRole("menuitem", { name: "导出" })).toBeDisabled();
    expect(progress).toHaveAttribute("aria-valuetext", "生成字幕，42%");
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
    expect(await screen.findByText("失败")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "识别失败但音频已就绪" })).toHaveAttribute(
      "href",
      "/tasks/cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    );
    await userEvent.click(screen.getByRole("button", { name: "识别失败但音频已就绪操作菜单" }));
    const exportButton = screen.getByRole("menuitem", { name: "导出" });
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
            asr: {
              mode: "auto",
              profile: {
                id: "tencent-asr-profile",
                profile_revision: 1,
                connection_revision: 1,
              },
            },
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
    const request = vi.spyOn(api, "request").mockResolvedValue({
      id: "44444444-4444-4444-8444-444444444444",
      status: "queued",
      updated_at: "2026-08-19T00:39:00Z",
      stage_runs: [],
    } as never);

    renderPage();

    expect(await screen.findByText("识别失败")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "云端失败且本地模型未安装" })).toHaveAttribute(
      "href",
      "/tasks/dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    );
    await userEvent.click(screen.getByRole("button", { name: "云端失败且本地模型未安装操作菜单" }));
    expect(screen.getByRole("menuitem", { name: "导出" })).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "重试云端失败且本地模型未安装" }),
    );
    expect(
      screen.getByText("将重新提交腾讯 ASR，可能再次产生费用。"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认重试" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/tasks/dddddddd-dddd-4ddd-8ddd-dddddddddddd/retry",
        {
          method: "POST",
          body: {
            item_id: "44444444-4444-4444-8444-444444444444",
            stage: "transcribe",
            expected_attempt: 1,
            strategy: "same",
            acknowledge_possible_charge: false,
          },
        },
      ),
    );
  });

  it("supports range, additive, current-page and keyboard selection", async () => {
    const tasks = [
      completedTask(
        "10000000-0000-4000-8000-000000000001",
        "20000000-0000-4000-8000-000000000001",
        "第一项",
      ),
      completedTask(
        "10000000-0000-4000-8000-000000000002",
        "20000000-0000-4000-8000-000000000002",
        "第二项",
      ),
      completedTask(
        "10000000-0000-4000-8000-000000000003",
        "20000000-0000-4000-8000-000000000003",
        "第三项",
      ),
      completedTask(
        "10000000-0000-4000-8000-000000000004",
        "20000000-0000-4000-8000-000000000004",
        "第四项",
      ),
    ];
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: tasks, nextCursor: null });

    renderPage();
    const firstRow = (await screen.findByRole("heading", { name: "第一项" })).closest(
      "article",
    );
    const secondRow = screen.getByRole("heading", { name: "第二项" }).closest("article");
    const thirdRow = screen.getByRole("heading", { name: "第三项" }).closest("article");
    expect(firstRow).not.toBeNull();
    expect(secondRow).not.toBeNull();
    expect(thirdRow).not.toBeNull();

    fireEvent.click(firstRow!);
    fireEvent.click(thirdRow!, { shiftKey: true });
    expect(screen.getByText("3/4 行被选中")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择第二项" })).toBeChecked();

    fireEvent.click(secondRow!, { ctrlKey: true });
    expect(screen.getByText("2/4 行被选中")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择第二项" })).not.toBeChecked();

    await userEvent.click(
      screen.getByRole("checkbox", { name: "选择当前页" }),
    );
    expect(screen.getByText("4/4 行被选中")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "取消选择当前页" }),
    );
    expect(screen.getByText("0/4 行被选中")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "选择第一项" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择第四项" }), {
      shiftKey: true,
    });
    expect(screen.getByText("4/4 行被选中")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择第四项" }), {
      shiftKey: true,
    });
    expect(screen.getByText("0/4 行被选中")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "选择第一项" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "选择第四项" }), {
      shiftKey: true,
    });
    expect(screen.getByText("4/4 行被选中")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.getByText("0/4 行被选中")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "选择第二项" }));
    fireEvent.keyDown(window, { key: "a", ctrlKey: true });
    expect(screen.getByText("4/4 行被选中")).toBeInTheDocument();
  });

  it("reports selection against the current page and deletes selected records from the header", async () => {
    const tasks = Array.from({ length: 11 }, (_, index) => {
      const suffix = String(index + 1).padStart(12, "0");
      return completedTask(
        `30000000-0000-4000-8000-${suffix}`,
        `40000000-0000-4000-8000-${suffix}`,
        `分页记录 ${index + 1}`,
      );
    });
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: tasks, nextCursor: null });
    const request = vi.spyOn(api, "request").mockResolvedValue({
      collections: [],
      tags: [],
    });

    renderPage();
    expect(await screen.findByRole("heading", { name: "分页记录 1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("combobox", { name: "每页行数" }));
    await userEvent.click(screen.getByRole("option", { name: "10" }));

    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页" }));
    expect(screen.getByText("10/10 行被选中")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(await screen.findByRole("heading", { name: "分页记录 11" })).toBeInTheDocument();
    expect(screen.getByText("0/1 行被选中")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页" }));
    expect(screen.getByText("1/1 行被选中")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    const dialog = screen.getByRole("dialog", { name: "删除选中的 11 条记录？" });
    await userEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      const deleteCalls = request.mock.calls.filter(
        ([path, options]) =>
          path.startsWith("/api/tasks/") && options?.method === "DELETE",
      );
      expect(deleteCalls).toHaveLength(11);
    });
  });

  it("loads every task cursor before calculating local pages", async () => {
    const first = completedTask(
      "50000000-0000-4000-8000-000000000001",
      "60000000-0000-4000-8000-000000000001",
      "第一页记录",
    );
    const second = completedTask(
      "50000000-0000-4000-8000-000000000002",
      "60000000-0000-4000-8000-000000000002",
      "后续游标记录",
    );
    const requestPage = vi.spyOn(api, "requestPage").mockImplementation(async (path) =>
      path.includes("cursor=next-page")
        ? { data: [second], nextCursor: null }
        : { data: [first], nextCursor: "next-page" },
    );

    renderPage();

    expect(await screen.findByRole("heading", { name: "第一页记录" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "后续游标记录" })).toBeInTheDocument();
    expect(screen.getByText("0/2 行被选中")).toBeInTheDocument();
    expect(requestPage).toHaveBeenCalledWith("/api/tasks?limit=100", undefined);
    expect(requestPage).toHaveBeenCalledWith(
      "/api/tasks?limit=100&cursor=next-page",
      undefined,
    );
  });

  it("confirms and deletes one terminal task", async () => {
    const task = completedTask(
      "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      "55555555-5555-4555-8555-555555555555",
      "待删除视频",
    );
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [task],
      nextCursor: null,
    });
    const request = vi.spyOn(api, "request").mockResolvedValue(undefined);

    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "待删除视频操作菜单" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "删除" }));
    const dialog = screen.getByRole("dialog", { name: "删除“待删除视频”？" });
    expect(dialog).toHaveTextContent("相关字幕、总结和缓存文件将永久删除。");
    await userEvent.click(within(dialog).getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/tasks/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        { method: "DELETE" },
      );
    });
    expect(screen.queryByRole("heading", { name: "待删除视频" })).not.toBeInTheDocument();
  });

  it("offers the requested collection, export, deletion and property controls", async () => {
    const first = completedTask(
      "ffffffff-ffff-4fff-8fff-ffffffffffff",
      "66666666-6666-4666-8666-666666666666",
      "第一条",
    );
    const second = completedTask(
      "77777777-7777-4777-8777-777777777777",
      "88888888-8888-4888-8888-888888888888",
      "第二条",
    );
    vi.spyOn(api, "requestPage").mockResolvedValue({
      data: [first, second],
      nextCursor: null,
    });
    vi.spyOn(api, "request").mockResolvedValue({ collections: [], tags: [] });

    renderPage();
    expect(await screen.findByRole("heading", { name: "总结记录" })).toBeInTheDocument();
    expect(screen.queryByText("Knowledge archive")).not.toBeInTheDocument();
    expect(screen.queryByText("集中查看视频总结、字幕来源与处理状态。")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("checkbox", { name: "选择当前页" }),
    );
    expect(screen.getByText("2/2 行被选中")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除" })).toBeEnabled();
    expect(screen.queryByText(/可添加合集或批量导出/u)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /清除/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "批量导出" }));
    expect(screen.getByRole("menuitem", { name: /导出总结（Markdown）/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /导出原文（Markdown）/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /导出总结和原文（ZIP）/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /导出 ZIP（仅总结）/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "添加到合集" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("radio", { name: /创建新合集/ })).toBeChecked();
    expect(within(dialog).getByRole("textbox", { name: "新合集名称" })).toBeInTheDocument();
    expect(within(dialog).queryByText(/已选择 .*项记录/u)).not.toBeInTheDocument();
    expect(within(dialog).queryByText("新建后立即加入所选记录")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("可同时选择多个合集")).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/公开/)).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await userEvent.click(screen.getByRole("button", { name: "属性" }));
    expect(screen.getByRole("menuitemcheckbox", { name: "封面" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemcheckbox", { name: "标题" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemcheckbox", { name: "发布时间" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("button", { name: "刷新" })).not.toBeInTheDocument();
  });

  it("opens a collection route as a filtered view and can return to all summaries", async () => {
    const task = completedTask(
      "99999999-9999-4999-8999-999999999999",
      "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      "合集内视频",
    );
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/library/meta") {
        return {
          collections: [{ id: "collection-1", name: "租房", task_count: 1 }],
          tags: [],
        } as never;
      }
      if (path.includes("/api/library/search?")) {
        return [{ task, match: null, collections: [], tags: [] }] as never;
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [task], nextCursor: null });

    renderPage("/tasks?collection_id=collection-1");

    expect(await screen.findByText(/正在查看合集/)).toHaveTextContent("租房");
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/library/search?limit=100&collection_id=collection-1",
      );
    });
    await userEvent.click(screen.getByRole("link", { name: "查看全部" }));
    await waitFor(() => {
      expect(screen.queryByText(/正在查看合集/)).not.toBeInTheDocument();
    });
  });

  it("keeps search results compact and uses the shared clear control", async () => {
    const task = completedTask(
      "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      "44444444-4444-4444-8444-444444444444",
      "毕业租房攻略",
    );
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/library/meta") return { collections: [], tags: [] } as never;
      if (path.includes("/api/library/search?")) {
        return [{
          task,
          match: {
            kind: "title",
            item_id: task.items[0].id,
            segment_id: null,
            start_ms: null,
            end_ms: null,
            snippet: "毕业租房攻略",
          },
          collections: [],
          tags: [],
        }] as never;
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [task], nextCursor: null });

    renderPage();
    const search = await screen.findByRole("textbox", { name: "搜索总结记录" });
    await userEvent.type(search, "毕业");

    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/api/library/search?limit=100&q=%E6%AF%95%E4%B8%9A",
    ));
    expect(screen.getAllByText("毕业租房攻略")).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "清除总结搜索" }));
    expect(search).toHaveValue("");
    expect(search).toHaveFocus();
  });

  it("opens the focused new-summary dialog from the page header", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [], nextCursor: null });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") {
        return {
          asr_mode: "auto",
          local_asr_engine: "faster_whisper",
          cloud_asr_profile_id: null,
          translation_enabled: false,
          translation_profile_id: null,
          translation_target_language: "zh-Hans",
          notes_enabled: true,
          notes_profile_id: "notes-profile",
          notes_template: "summary",
          notes_output_language: "zh-Hans",
          has_custom_prompt: false,
          local_whisper_options: {},
        } as never;
      }
      if (path === "/api/profiles") {
        return [{
          id: "notes-profile",
          name: "总结模型",
          purpose: "notes",
          connection_id: "connection",
          protocol: "openai_chat_completions",
          base_url: "https://example.invalid/v1",
          model: "model",
          context_length: 32_000,
          options: {},
          revision: 1,
          tested: true,
          test_ok: true,
          test_message: null,
          upload_authorized: false,
          capability_fingerprint: null,
          chat_data_authorized: true,
        }] as never;
      }
      return { collections: [], tags: [] } as never;
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "新增" }));

    const dialog = screen.getByRole("dialog", {
      name: "把音视频变成字幕和笔记",
    });
    expect(within(dialog).getByRole("tab", { name: "链接" })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByRole("tab", { name: "上传" })).toBeInTheDocument();
    expect(within(dialog).getByText("支持 B 站、抖音、YouTube 链接和本地文件。")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "总结设置" })).toBeInTheDocument();
    expect(
      await within(dialog).findByRole("textbox", { name: "视频链接或分享文本" }),
    ).toHaveAttribute(
      "placeholder",
      "粘贴视频链接或分享文字（Enter 提交，Shift+Enter 换行）",
    );
    expect(within(dialog).getByRole("button", { name: "粘贴剪贴板内容" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "开始处理" })).toBeDisabled();
  });
});

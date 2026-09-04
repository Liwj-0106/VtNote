import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Task } from "../api/types";
import { RouterProvider } from "../app/router";
import { TaskDetailPage } from "./TaskDetailPage";

const mocks = vi.hoisted(() => ({
  task: null as Task | null,
  refresh: vi.fn(),
  notify: vi.fn(),
  registerTasks: vi.fn(),
  setResourceData: vi.fn(),
  resources: new Map<string, unknown>(),
}));

vi.mock("../app/hooks", () => ({
  useTaskPolling: () => ({
    task: mocks.task,
    error: null,
    refresh: mocks.refresh,
  }),
  useApiResource: (path: string | null) => ({
    data: path ? (mocks.resources.get(path) ?? null) : null,
    error: null,
    loading: false,
    refresh: vi.fn(),
    setData: mocks.setResourceData,
  }),
}));

vi.mock("../features/task-queue/TaskQueueProvider", () => ({
  useTaskQueue: () => ({
    notify: mocks.notify,
    registerTasks: mocks.registerTasks,
  }),
}));

vi.mock("../features/task-detail/SourceVideoPanel", () => ({
  SourceVideoPanel: () => <div data-testid="source-video" />,
}));

function failedNotesTask(): Task {
  const timestamp = "2026-08-29T08:00:00Z";
  return {
    id: "11111111-1111-4111-8111-111111111111",
    status: "completed_with_warnings",
    options: {
      output_type: "notes",
      notes_enabled: true,
    },
    pipeline_snapshot: {
      output_type: "notes",
      notes: { enabled: true },
    },
    terminal_reason_code: null,
    created_at: timestamp,
    updated_at: timestamp,
    items: [
      {
        id: "22222222-2222-4222-8222-222222222222",
        position: 0,
        source_kind: "local_media",
        source_locator: "sample.mp4",
        source_display_name: "sample.mp4",
        status: "completed_with_warnings",
        title: "sample",
        created_at: timestamp,
        updated_at: timestamp,
        stage_runs: [
          {
            id: "33333333-3333-4333-8333-333333333333",
            stage: "notes",
            attempt: 1,
            status: "failed",
            error_code: "chat_output_truncated",
            error_message: "chat_output_truncated",
            warning: null,
            progress: null,
            execution_evidence: null,
            provider_status_code: null,
            external_submission_state: null,
            started_at: timestamp,
            finished_at: timestamp,
            created_at: timestamp,
            updated_at: timestamp,
          },
        ],
      },
    ],
  };
}

function originalTask(): Task {
  const timestamp = "2026-08-29T08:00:00Z";
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    status: "completed",
    options: { output_type: "transcript" },
    pipeline_snapshot: {},
    terminal_reason_code: null,
    created_at: timestamp,
    updated_at: timestamp,
    items: [
      {
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        position: 0,
        source_kind: "bilibili",
        source_locator: "https://www.bilibili.com/video/BV1example01",
        source_display_name: "测试视频",
        status: "completed",
        title: "测试视频",
        created_at: timestamp,
        updated_at: timestamp,
        stage_runs: [
          {
            id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            stage: "transcribe",
            attempt: 1,
            status: "completed",
            error_code: null,
            error_message: null,
            warning: null,
            progress: null,
            execution_evidence: null,
            provider_status_code: null,
            external_submission_state: null,
            started_at: timestamp,
            finished_at: timestamp,
            created_at: timestamp,
            updated_at: timestamp,
          },
        ],
      },
    ],
  };
}

function completedNotesTask(): Task {
  const task = originalTask();
  task.options = { output_type: "notes", notes_enabled: true };
  task.pipeline_snapshot = { notes: { enabled: true } };
  task.items[0].stage_runs.push({
    ...task.items[0].stage_runs[0],
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    stage: "notes",
  });
  return task;
}

describe("TaskDetailPage notes failure", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
    };
    mocks.task = failedNotesTask();
    mocks.refresh.mockReset();
    mocks.notify.mockReset();
    mocks.registerTasks.mockReset();
    mocks.setResourceData.mockReset();
    mocks.resources.clear();
    localStorage.clear();
    vi.restoreAllMocks();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("shows a concise failure and moves the retried summary into the queue", async () => {
    const retriedItem = {
      ...failedNotesTask().items[0],
      status: "queued",
      stage_runs: [
        ...failedNotesTask().items[0].stage_runs,
        {
          ...failedNotesTask().items[0].stage_runs[0],
          id: "44444444-4444-4444-8444-444444444444",
          attempt: 2,
          status: "queued",
          error_code: null,
          error_message: null,
          started_at: null,
          finished_at: null,
        },
      ],
    };
    const queuedTask = {
      ...failedNotesTask(),
      status: "queued",
      updated_at: retriedItem.updated_at,
      items: [retriedItem],
    };
    const request = vi.spyOn(api, "request").mockResolvedValue(retriedItem);
    render(
      <RouterProvider initialPath="/tasks/11111111-1111-4111-8111-111111111111?tab=notes">
        <TaskDetailPage taskId="11111111-1111-4111-8111-111111111111" />
      </RouterProvider>,
    );

    expect(screen.getByRole("heading", { name: "总结未生成" })).toBeInTheDocument();
    expect(screen.queryByText(/模型输出/u)).not.toBeInTheDocument();
    expect(screen.queryByText(/字幕处理/u)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重新生成总结" }));

    expect(request).toHaveBeenCalledWith(
      "/api/tasks/11111111-1111-4111-8111-111111111111/retry",
      {
        method: "POST",
        body: {
          item_id: "22222222-2222-4222-8222-222222222222",
          stage: "notes",
          expected_attempt: 1,
          strategy: "same",
          acknowledge_possible_charge: false,
        },
      },
    );
    expect(mocks.registerTasks).toHaveBeenCalledWith([queuedTask]);
    expect(mocks.refresh).toHaveBeenCalledOnce();
  });

  it("uses the reference two-row toolbar and opens the collection picker", async () => {
    mocks.task = originalTask();
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/transcript",
      {
        segments: [
          { id: "cue-1", start_ms: 0, end_ms: 8_000, text: "第一章开场内容。" },
          { id: "cue-2", start_ms: 350_000, end_ms: 360_000, text: "第二章租房经验。" },
        ],
      },
    );
    const request = vi
      .spyOn(api, "request")
      .mockResolvedValue({ collections: [], tags: [] });

    render(
      <RouterProvider initialPath="/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?tab=original">
        <TaskDetailPage taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />
      </RouterProvider>,
    );

    expect(screen.getByRole("tab", { name: "原文" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("返回内容库")).not.toBeInTheDocument();
    const primaryToolbar = document.querySelector(".detail-primary-toolbar");
    expect(primaryToolbar).toHaveTextContent("总结设置");
    expect(primaryToolbar).toHaveTextContent("默认模型");
    expect(primaryToolbar).toContainElement(
      screen.getByRole("tab", { name: "原文" }),
    );
    expect(screen.getByRole("checkbox", { name: "字幕滚动" })).toBeChecked();
    expect(screen.getByRole("button", { name: "添加合集" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("更多操作")).not.toBeInTheDocument();
    expect(document.querySelector(".result-chapter-trigger")).toHaveTextContent(
      "章节 (2)",
    );
    expect(screen.queryByText("视频原文")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "添加合集" }));
    expect(await screen.findByRole("heading", { name: "添加到合集" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith("/api/library/meta");
  });

  it("shows the assigned collection state and keeps copy and download", () => {
    mocks.task = originalTask();
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/transcript",
      { segments: [{ id: "cue-1", start_ms: 0, end_ms: 8_000, text: "开场内容。" }] },
    );
    mocks.resources.set(
      "/api/library/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      { collections: [{ id: "collection-1", name: "租房" }], tags: [] },
    );

    render(
      <RouterProvider initialPath="/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?tab=original">
        <TaskDetailPage taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />
      </RouterProvider>,
    );

    expect(screen.getByRole("link", { name: "租房" })).toHaveAttribute(
      "href",
      "/collections/collection-1",
    );
    expect(screen.getByRole("button", { name: "管理合集" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加合集" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("更多操作")).not.toBeInTheDocument();
  });

  it("uses the unified toolbar and re-summary action for a completed summary", async () => {
    const task = completedNotesTask();
    mocks.task = task;
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/transcript",
      {
        segments: [
          { id: "cue-1", start_ms: 0, end_ms: 8_000, text: "开场内容。" },
          {
            id: "cue-2",
            start_ms: 350_000,
            end_ms: 360_000,
            text: "第二章租房经验。",
          },
        ],
      },
    );
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/notes",
      [{ id: "note-1", markdown: "# 摘要\n\n> AI 生成内容：请核对人名、数字、术语和引用。\n\n租房建议。" }],
    );
    mocks.resources.set(
      "/api/library/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      { collections: [], tags: [] },
    );
    const queuedItem = { ...task.items[0], status: "queued" };
    const request = vi.spyOn(api, "request").mockResolvedValue(queuedItem);

    render(
      <RouterProvider initialPath="/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?tab=notes">
        <TaskDetailPage taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />
      </RouterProvider>,
    );

    expect(screen.getByText("总结完成")).toBeInTheDocument();
    expect(screen.queryByText(/AI 生成内容/u)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新总结" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加合集" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "复制" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("更多操作")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "阅读全文" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "原文" })).toBeInTheDocument();

    const chapterMenu = document.querySelector<HTMLElement>(
      ".result-chapter-menu",
    );
    expect(chapterMenu).not.toBeNull();
    const chapterTrigger = within(chapterMenu as HTMLElement).getByRole("button", {
      name: /章节/u,
    });
    await userEvent.click(chapterTrigger);
    expect(chapterTrigger).toHaveAttribute("aria-expanded", "true");
    expect(
      within(chapterMenu as HTMLElement).getByText("章节目录"),
    ).toBeInTheDocument();
    await userEvent.click(
      within(chapterMenu as HTMLElement).getByRole("menuitem", {
        name: /05:50.*第二章租房经验/u,
      }),
    );
    expect(screen.queryByRole("menu", { name: "章节目录" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "全文总结" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      within(document.getElementById("original-chapter-2") as HTMLElement).getByRole(
        "button",
        { name: "收起原文" },
      ),
    ).toBeInTheDocument();
    expect(document.activeElement).toBe(
      document.getElementById("original-chapter-2"),
    );

    await userEvent.click(screen.getByRole("button", { name: "重新总结" }));
    expect(request).toHaveBeenCalledWith(
      "/api/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/retry",
      {
        method: "POST",
        body: {
          item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          stage: "notes",
          expected_attempt: 1,
          strategy: "same",
          acknowledge_possible_charge: false,
        },
      },
    );
    expect(mocks.setResourceData).toHaveBeenCalledWith(null);
  });

  it("selects the model in a concise summary settings dialog for the next retry", async () => {
    const task = completedNotesTask();
    task.pipeline_snapshot = {
      notes: {
        enabled: true,
        output_language: "zh-Hans",
        profile: {
          id: "notes-profile-a",
          model: "glm-5.1",
          profile_revision: 1,
        },
      },
    };
    mocks.task = task;
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/transcript",
      { segments: [{ id: "cue-1", start_ms: 0, end_ms: 8_000, text: "开场内容。" }] },
    );
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/notes",
      [{ id: "note-1", markdown: "# 摘要\n\n租房建议。" }],
    );
    mocks.resources.set(
      "/api/library/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      { collections: [], tags: [] },
    );
    const profiles = [
      {
        id: "notes-profile-a",
        name: "默认总结",
        purpose: "notes",
        connection_id: "connection-a",
        protocol: "tencent_tokenhub",
        base_url: "https://example.invalid",
        model: "glm-5.1",
        context_length: 64_000,
        options: {},
        revision: 1,
        tested: true,
        test_ok: true,
        test_message: null,
        upload_authorized: false,
        capability_fingerprint: {},
        chat_data_authorized: true,
      },
      {
        id: "notes-profile-b",
        name: "备用总结",
        purpose: "notes",
        connection_id: "connection-b",
        protocol: "openai_chat_completions",
        base_url: "https://example.invalid",
        model: "gpt-4.1-mini",
        context_length: 64_000,
        options: {},
        revision: 3,
        tested: true,
        test_ok: true,
        test_message: null,
        upload_authorized: false,
        capability_fingerprint: {},
        chat_data_authorized: true,
      },
    ];
    const queuedItem = { ...task.items[0], status: "queued" };
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/profiles") return profiles;
      return queuedItem;
    });

    render(
      <RouterProvider initialPath="/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?tab=notes">
        <TaskDetailPage taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />
      </RouterProvider>,
    );

    expect(document.querySelector(".detail-settings-toolbar")).toHaveTextContent(
      "glm-5.1",
    );
    await userEvent.click(screen.getByRole("button", { name: "总结设置" }));
    const dialog = await screen.findByRole("dialog", { name: "总结设置" });
    expect(within(dialog).getByLabelText("总结模型")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("总结方式")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("输出语言")).toBeInTheDocument();
    expect(within(dialog).queryByRole("paragraph")).not.toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole("combobox", { name: "总结模型" }));
    await userEvent.click(
      within(dialog).getByRole("option", { name: "备用总结 · gpt-4.1-mini" }),
    );
    await userEvent.click(within(dialog).getByRole("combobox", { name: "输出语言" }));
    await userEvent.click(within(dialog).getByRole("option", { name: "English" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(mocks.notify).toHaveBeenCalledWith("总结设置已保存");
    expect(document.querySelector(".detail-settings-toolbar")).toHaveTextContent(
      "gpt-4.1-mini",
    );

    await userEvent.click(screen.getByRole("button", { name: "重新总结" }));
    expect(request).toHaveBeenCalledWith(
      "/api/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/retry",
      {
        method: "POST",
        body: {
          item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          stage: "notes",
          expected_attempt: 1,
          strategy: "same",
          acknowledge_possible_charge: false,
          notes_profile_id: "notes-profile-b",
          notes_profile_revision: 3,
          notes_output_language: "en",
        },
      },
    );
  });

  it("uses the sticky subtitle toolbar and groups raw fragments into readable rows", async () => {
    mocks.task = originalTask();
    localStorage.setItem("vtnote.detail.subtitleGroupSize", "1");
    mocks.resources.set(
      "/api/items/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/transcript",
      {
        segments: [
          { id: "cue-1", start_ms: 0, end_ms: 600, text: "哈喽，" },
          { id: "cue-2", start_ms: 600, end_ms: 1_300, text: "这里是请匠，" },
          {
            id: "cue-3",
            start_ms: 1_300,
            end_ms: 3_000,
            text: "今天带你们走进租房小能手。",
          },
          {
            id: "cue-4",
            start_ms: 3_000,
            end_ms: 5_500,
            text: "我只花2600块钱租到带屋顶花园的房子。",
          },
        ],
      },
    );

    render(
      <RouterProvider initialPath="/tasks/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa?tab=transcript">
        <TaskDetailPage taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />
      </RouterProvider>,
    );

    expect(screen.getByRole("tab", { name: "字幕" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("slider", { name: "字幕分组条数" })).toHaveAttribute(
      "min",
      "1",
    );
    expect(screen.getByRole("slider", { name: "字幕分组条数" })).toHaveAttribute(
      "max",
      "20",
    );
    expect(screen.getByText("1 条", { selector: "output" })).toBeInTheDocument();
    expect(
      screen.getByText("哈喽，这里是请匠，今天带你们走进租房小能手。"),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId("transcript-cue")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "添加合集" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "导出" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "分享" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("更多操作")).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "音频播放" })).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "摘录" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "切换到完整文本模式" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    expect(screen.getByText("SRT")).toBeInTheDocument();
    expect(screen.getByText("通用字幕")).toBeInTheDocument();
    expect(screen.getByText("VTT")).toBeInTheDocument();
    expect(screen.getByText("JSON")).toBeInTheDocument();
    expect(screen.getByText("TXT")).toBeInTheDocument();
    expect(screen.getByText("LRC")).toBeInTheDocument();
    expect(screen.queryByText("Markdown")).not.toBeInTheDocument();
  });
});

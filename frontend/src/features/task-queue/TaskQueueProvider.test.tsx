import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { StageRun, Task } from "../../api/types";
import { RouterProvider } from "../../app/router";
import { TaskQueueProvider, useTaskQueue } from "./TaskQueueProvider";

function stageRun(
  id: string,
  stage: StageRun["stage"],
  status: string,
): StageRun {
  return {
    id,
    stage,
    attempt: 1,
    status,
    error_code: null,
    error_message: null,
    warning: null,
    progress:
      status === "running"
        ? {
            current: 4,
            total: 10,
            unit: "segments",
            message_code: "transcribing",
          }
        : null,
    execution_evidence: null,
    provider_status_code: null,
    external_submission_state: null,
    started_at: "2026-08-29T08:00:00Z",
    finished_at: status === "completed" ? "2026-08-29T08:01:00Z" : null,
    created_at: "2026-08-29T08:00:00Z",
    updated_at: "2026-08-29T08:01:00Z",
  };
}

function task(id: string, status: string, title: string): Task {
  return {
    id,
    status,
    options: {},
    pipeline_snapshot: {},
    terminal_reason_code: null,
    created_at: "2026-08-29T08:00:00Z",
    updated_at: "2026-08-29T08:00:00Z",
    items: [
      {
        id: `${id}-item`,
        position: 0,
        source_kind: "bilibili",
        source_locator: "https://www.bilibili.com/video/BV1xx411c7mD/",
        source_display_name: title,
        status,
        title,
        created_at: "2026-08-29T08:00:00Z",
        updated_at: "2026-08-29T08:00:00Z",
        stage_runs:
          status === "running"
            ? [stageRun(`${id}-transcript`, "transcribe", "running")]
            : status === "completed"
              ? [
                  stageRun(`${id}-transcript`, "transcribe", "completed"),
                  stageRun(`${id}-notes`, "notes", "completed"),
                ]
              : [],
      },
    ],
  };
}

function summaryTask(status: "completed_with_warnings" | "queued" | "completed"): Task {
  const result = task(
    "55555555-5555-4555-8555-555555555555",
    status,
    "总结状态测试",
  );
  result.options = { output_type: "notes", notes_enabled: true };
  result.pipeline_snapshot = { notes: { enabled: true } };
  const failedRun = {
    ...stageRun("summary-failed", "notes", "failed"),
    error_code: "chat_output_truncated",
  };
  result.items[0].stage_runs = [
    stageRun("summary-transcript", "transcribe", "completed"),
    failedRun,
  ];
  if (status !== "completed_with_warnings") {
    result.items[0].stage_runs.push({
      ...stageRun(
        "summary-retry",
        "notes",
        status === "completed" ? "completed" : "queued",
      ),
      attempt: 2,
    });
  }
  return result;
}

function QueueHarness() {
  const { registerTasks } = useTaskQueue();
  return (
    <button
      type="button"
      onClick={() =>
        registerTasks([
          task("11111111-1111-4111-8111-111111111111", "running", "正在处理的视频"),
          task("22222222-2222-4222-8222-222222222222", "completed", "已完成的视频"),
          task("33333333-3333-4333-8333-333333333333", "failed", "失败的视频"),
        ])
      }
    >
      添加任务
    </button>
  );
}

function SummaryStatusHarness() {
  const { registerTasks } = useTaskQueue();
  return (
    <>
      <button type="button" onClick={() => registerTasks([summaryTask("completed_with_warnings")])}>
        添加总结失败任务
      </button>
      <button type="button" onClick={() => registerTasks([summaryTask("queued")])}>
        重试总结
      </button>
      <button type="button" onClick={() => registerTasks([summaryTask("completed")])}>
        完成总结
      </button>
    </>
  );
}

function PasteHarness() {
  const { pendingPaste } = useTaskQueue();
  return <textarea aria-label="新任务链接" value={pendingPaste ?? ""} readOnly />;
}

describe("TaskQueueProvider", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    if (!("PointerEvent" in window)) {
      class PointerEventMock extends MouseEvent {
        pointerId: number;
        pointerType: string;

        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerId = init.pointerId ?? 0;
          this.pointerType = init.pointerType ?? "mouse";
        }
      }
      Object.defineProperty(window, "PointerEvent", {
        configurable: true,
        value: PointerEventMock,
      });
    }
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [], nextCursor: null });
  });

  it("shows the reference queue actions, external title, collapse control, and toast", async () => {
    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <QueueHarness />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加任务" }));

    expect(screen.getByText("正在处理的视频")).toBeInTheDocument();
    expect(screen.getByText("处理中")).toBeInTheDocument();
    expect(screen.getByText("已完成的视频")).toBeInTheDocument();
    expect(screen.getByText("失败的视频")).toBeInTheDocument();
    expect(screen.getByText("生成字幕…")).toBeInTheDocument();
    expect(screen.getByText(/40%/u)).toBeInTheDocument();
    expect(screen.getByLabelText("通知")).toHaveTextContent("3 个任务正在处理");
    expect(screen.getByText("处理队列 (3)")).toBeInTheDocument();

    const sourceLink = screen.getByRole("link", { name: "已完成的视频" });
    expect(sourceLink).toHaveAttribute(
      "href",
      "https://www.bilibili.com/video/BV1xx411c7mD",
    );
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "查看总结" })).toHaveAttribute(
      "href",
      "/tasks/22222222-2222-4222-8222-222222222222?tab=notes",
    );
    expect(screen.getByRole("link", { name: "查看字幕" })).toHaveAttribute(
      "href",
      "/tasks/22222222-2222-4222-8222-222222222222?tab=transcript",
    );
    expect(
      screen.getByRole("link", { name: "继续添加新内容" }),
    ).toHaveAttribute("href", "/");

    const close = screen.getByRole("button", { name: "收起处理队列" });
    expect(close).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(close);
    expect(
      screen.getByRole("button", { name: "展开处理队列" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("正在处理的视频")).not.toBeInTheDocument();
    expect(screen.getByText("处理队列")).toBeInTheDocument();
    expect(screen.queryByText(/3 完成/u)).not.toBeInTheDocument();
    expect(localStorage.getItem("vtnote.taskQueue.collapsed")).toBe("true");

  });

  it("moves a missing summary from failed to processing and then completed", async () => {
    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <SummaryStatusHarness />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加总结失败任务" }));
    expect(screen.getByText("总结失败")).toBeInTheDocument();
    expect(screen.getByText("1 失败")).toBeInTheDocument();
    expect(screen.queryByText("完成")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看详情" })).toHaveAttribute(
      "href",
      "/tasks/55555555-5555-4555-8555-555555555555?tab=notes",
    );

    await userEvent.click(screen.getByRole("button", { name: "重试总结" }));
    expect(screen.getByText("生成总结…")).toBeInTheDocument();
    expect(screen.getByText("1 处理中")).toBeInTheDocument();
    expect(screen.queryByText("总结失败")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "完成总结" }));
    expect(screen.getByText("1 完成")).toBeInTheDocument();
    expect(screen.getByText("完成")).toBeInTheDocument();
  });

  it("closes the queue window and opens it again for a new task", async () => {
    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <QueueHarness />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加任务" }));
    await userEvent.click(
      screen.getByRole("button", { name: "关闭处理队列" }),
    );
    expect(screen.queryByLabelText("处理队列")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "添加任务" }));
    expect(screen.getByLabelText("处理队列")).toBeInTheDocument();
  });

  it("fills the new-task input from the quick-paste action", async () => {
    const clipboardText = "https://www.bilibili.com/video/BV1xx411c7mD/";
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText: vi.fn().mockResolvedValue(clipboardText) },
    });

    render(
      <RouterProvider initialPath="/tasks">
        <TaskQueueProvider>
          <QueueHarness />
          <PasteHarness />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加任务" }));
    await userEvent.click(
      screen.getByRole("button", { name: "快速粘贴链接" }),
    );

    expect(await screen.findByLabelText("新任务链接")).toHaveValue(clipboardText);
    expect(screen.queryByLabelText("提示")).not.toBeInTheDocument();
  });

  it("drags both the open and collapsed queue without toggling by mistake", async () => {
    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <QueueHarness />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加任务" }));
    const dock = screen.getByLabelText("处理队列");
    vi.spyOn(dock, "getBoundingClientRect").mockReturnValue({
      x: 100,
      y: 50,
      left: 100,
      top: 50,
      right: 556,
      bottom: 430,
      width: 456,
      height: 380,
      toJSON: () => undefined,
    });
    const header = dock.querySelector(".task-queue-header");
    expect(header).not.toBeNull();

    fireEvent.pointerDown(header as Element, {
      pointerId: 7,
      pointerType: "mouse",
      button: 0,
      clientX: 140,
      clientY: 70,
    });
    fireEvent.pointerMove(header as Element, {
      pointerId: 7,
      pointerType: "mouse",
      clientX: 260,
      clientY: 160,
    });
    fireEvent.pointerUp(header as Element, {
      pointerId: 7,
      pointerType: "mouse",
      clientX: 260,
      clientY: 160,
    });

    await waitFor(() => {
      expect(dock).toHaveStyle({ left: "220px", top: "140px" });
    });
    expect(localStorage.getItem("vtnote.taskQueue.position")).toBe(
      JSON.stringify({ x: 220, y: 140 }),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "收起处理队列" }),
    );
    const collapsedDock = screen.getByLabelText("处理队列");
    vi.spyOn(collapsedDock, "getBoundingClientRect").mockReturnValue({
      x: 220,
      y: 140,
      left: 220,
      top: 140,
      right: 514,
      bottom: 192,
      width: 294,
      height: 52,
      toJSON: () => undefined,
    });
    const collapsedToggle = screen.getByRole("button", {
      name: "展开处理队列",
    });

    fireEvent.pointerDown(collapsedToggle, {
      pointerId: 8,
      pointerType: "mouse",
      button: 0,
      clientX: 260,
      clientY: 160,
    });
    fireEvent.pointerMove(collapsedToggle, {
      pointerId: 8,
      pointerType: "mouse",
      clientX: 360,
      clientY: 240,
    });
    fireEvent.pointerUp(collapsedToggle, {
      pointerId: 8,
      pointerType: "mouse",
      clientX: 360,
      clientY: 240,
    });
    fireEvent.click(collapsedToggle);

    await waitFor(() => {
      expect(collapsedDock).toHaveStyle({ left: "320px", top: "220px" });
    });
    expect(
      screen.getByRole("button", { name: "展开处理队列" }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("vtnote.taskQueue.position")).toBe(
      JSON.stringify({ x: 320, y: 220 }),
    );

    await userEvent.click(collapsedToggle);
    expect(
      screen.getByRole("button", { name: "收起处理队列" }),
    ).toBeInTheDocument();
  });

  it("updates a terminal task and creates a completion toast", async () => {
    vi.mocked(api.requestPage)
      .mockResolvedValueOnce({
        data: [
          task(
            "44444444-4444-4444-8444-444444444444",
            "running",
            "后台视频",
          ),
        ],
        nextCursor: null,
      })
      .mockResolvedValue({
        data: [
          task(
            "44444444-4444-4444-8444-444444444444",
            "completed",
            "后台视频",
          ),
        ],
        nextCursor: null,
      });

    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <p>页面内容</p>
        </TaskQueueProvider>
      </RouterProvider>,
    );

    expect(await screen.findByText("后台视频")).toBeInTheDocument();
    const completionToast = await screen.findByRole(
      "status",
      { name: "“后台视频”总结完成" },
      { timeout: 2_500 },
    );
    expect(
      within(completionToast).getByRole("link", { name: "查看总结" }),
    ).toHaveAttribute(
      "href",
      "/tasks/44444444-4444-4444-8444-444444444444?tab=notes",
    );
    expect(completionToast).toHaveTextContent("“后台视频”总结完成");
    expect(screen.queryByText(/stage|transcribe|message_code/iu)).not.toBeInTheDocument();
  });

  it("uses the shared source-failure label in the queue and toast", async () => {
    const active = task(
      "66666666-6666-4666-8666-666666666666",
      "running",
      "来源异常视频",
    );
    active.items[0].stage_runs = [
      stageRun("source-active", "source", "running"),
    ];
    const failed = task(active.id, "failed", "来源异常视频");
    failed.items[0].stage_runs = [
      stageRun("source-failed", "source", "failed"),
    ];
    vi.mocked(api.requestPage)
      .mockResolvedValueOnce({ data: [active], nextCursor: null })
      .mockResolvedValue({ data: [failed], nextCursor: null });

    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <p>页面内容</p>
        </TaskQueueProvider>
      </RouterProvider>,
    );

    expect(await screen.findByText("来源异常视频")).toBeInTheDocument();
    const failureToast = await screen.findByRole("alert", undefined, {
      timeout: 2_500,
    });
    expect(failureToast).toHaveTextContent("来源失败");
    expect(
      within(screen.getByLabelText("处理队列")).getByText("来源失败"),
    ).toBeInTheDocument();
    expect(screen.getByText("1 失败")).toBeInTheDocument();
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import type { Task } from "../api/types";
import { savePreferences } from "../app/preferences";
import { RouterProvider, useRouter } from "../app/router";
import { TaskQueueProvider } from "../features/task-queue/TaskQueueProvider";
import { CreateTaskPage } from "./CreateTaskPage";

const defaults = {
  asr_mode: "auto",
  local_asr_engine: "faster_whisper",
  cloud_asr_profile_id: null,
  translation_enabled: false,
  translation_profile_id: null,
  translation_target_language: "zh-Hans",
  notes_enabled: false,
  notes_profile_id: null,
  notes_template: "summary",
  notes_output_language: "zh-Hans",
  has_custom_prompt: false,
  local_whisper_options: {},
};

const notesProfile = {
  id: "notes-profile",
  purpose: "notes",
  tested: true,
  test_ok: true,
  chat_data_authorized: true,
};

function sourceProbe() {
  return {
    result_type: "single",
    source_kind: "bilibili",
    canonical_url: "https://www.bilibili.com/video/BV1x",
    title: "课程视频",
    duration_ms: 120000,
    subtitle_tracks: [],
  };
}

function collectionProbe() {
  return {
    result_type: "collection",
    source_kind: "bilibili",
    canonical_url:
      "https://space.bilibili.com/2142762/lists/3662502?type=season",
    title: "前端课程",
    duration_ms: null,
    subtitle_tracks: [],
    collection: {
      id: "bilibili:season:2142762:3662502",
      title: "前端课程",
      total_items: 3,
      truncated: false,
      items: [
        {
          id: "BV1111111111",
          canonical_url: "https://www.bilibili.com/video/BV1111111111",
          title: "HTML 基础",
          duration_ms: 61_000,
        },
        {
          id: "BV2222222222",
          canonical_url: "https://www.bilibili.com/video/BV2222222222",
          title: "CSS 布局",
          duration_ms: 122_000,
        },
        {
          id: "BV3333333333",
          canonical_url: "https://www.bilibili.com/video/BV3333333333",
          title: "React 状态",
          duration_ms: null,
        },
      ],
    },
  };
}

function createdTask(): Task {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    status: "queued",
    options: {},
    pipeline_snapshot: {},
    terminal_reason_code: null,
    created_at: "2026-08-29T08:00:00Z",
    updated_at: "2026-08-29T08:00:00Z",
    items: [
      {
        id: "11111111-1111-4111-8111-111111111112",
        position: 0,
        source_kind: "bilibili",
        source_locator: "https://www.bilibili.com/video/BV1x",
        source_display_name: "课程视频",
        status: "queued",
        title: "课程视频",
        stage_runs: [],
        created_at: "2026-08-29T08:00:00Z",
        updated_at: "2026-08-29T08:00:00Z",
      },
    ],
  };
}

async function enterAndSubmit() {
  await userEvent.type(
    screen.getByLabelText("视频链接或分享文本"),
    "https://www.bilibili.com/video/BV1x",
  );
  await userEvent.click(screen.getByRole("button", { name: "开始处理" }));
}

function CurrentPath() {
  const { path } = useRouter();
  return <output data-testid="current-path">{path}</output>;
}

describe("CreateTaskPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute("open");
    };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
  });

  it("shows the compact launcher with summary settings", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );

    expect(await screen.findByRole("tab", { name: "链接" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "上传" })).toBeInTheDocument();
    expect(
      screen.queryByText("支持 B 站、抖音、YouTube 链接和本地文件。"),
    ).not.toBeInTheDocument();
    expect(document.querySelector('label[for="source-url"]')).toHaveClass(
      "visually-hidden",
    );
    expect(screen.getByLabelText("视频链接或分享文本")).toHaveAttribute(
      "placeholder",
      "粘贴视频链接或分享文字",
    );
    expect(screen.getByText("Enter 提交 · Shift+Enter 换行")).toHaveClass(
      "visually-hidden",
    );
    expect(
      screen.getByRole("button", { name: "粘贴剪贴板内容" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "总结设置" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始处理" })).toHaveTextContent(
      "开始处理",
    );
    expect(screen.queryByText("粘贴")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "上传" }));
    expect(screen.getByText("上传文件")).toBeInTheDocument();
    expect(screen.queryByText("从电脑中选择，不会显示本机路径")).not.toBeInTheDocument();
    expect(document.querySelector<HTMLInputElement>("input[type=file]")?.accept).toContain(
      ".aac",
    );
    expect(document.querySelector<HTMLInputElement>("input[type=file]")?.accept).toContain(
      ".srt",
    );
    expect(document.querySelector<HTMLInputElement>("input[type=file]")?.accept).toContain(
      ".txt",
    );
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByText("选择导出类型")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "粘贴剪贴板内容" }),
    ).not.toBeInTheDocument();
  });

  it("fills the link field from the clipboard button", async () => {
    const readText = vi.fn().mockResolvedValue("https://b23.tv/AbC123");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { readText },
    });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "粘贴剪贴板内容" }),
    );

    expect(readText).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("视频链接或分享文本")).toHaveValue(
      "https://b23.tv/AbC123",
    );
  });

  it("previews Markdown links and creates only the selected valid tasks", async () => {
    let batchBody: unknown;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe-batch") {
        return {
          results: [
            {
              input_url: "https://youtu.be/abc",
              canonical_url: "https://youtu.be/abc",
              title: "YouTube 课程",
              source_kind: "youtube",
              status: "ready",
            },
            {
              input_url: "https://www.bilibili.com/video/BV1second",
              canonical_url: "https://www.bilibili.com/video/BV1second",
              title: "B 站课程",
              source_kind: "bilibili",
              status: "ready",
            },
          ],
          valid_sources: [
            { kind: "youtube", url: "https://youtu.be/abc" },
            { kind: "bilibili", url: "https://www.bilibili.com/video/BV1second" },
          ],
        };
      }
      if (path === "/api/tasks/batch") {
        batchBody = options?.body;
        return [];
      }
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    const input = await screen.findByLabelText("视频链接或分享文本");
    fireEvent.change(input, {
      target: {
        value:
          "- [YouTube 课程](https://youtu.be/abc)\n- [B 站课程](https://www.bilibili.com/video/BV1second)",
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));
    expect(await screen.findByText("YouTube 课程")).toBeInTheDocument();
    expect(screen.getByText("B 站课程")).toBeInTheDocument();
    expect(screen.getByText(/已选择 2 个/u)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "YouTube 课程" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "B 站课程" })).toBeChecked();

    await userEvent.click(screen.getByRole("checkbox", { name: "B 站课程" }));
    expect(screen.getByText(/已选择 1 个/u)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "开始处理 1 个视频" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/tasks/batch",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(batchBody).toMatchObject({
      sources: [{ kind: "url", locator: "https://youtu.be/abc" }],
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("shows the number of links while a batch preview is loading", async () => {
    let resolveProbe: (value: { results: never[]; valid_sources: never[] }) => void = () => {};
    const pendingProbe = new Promise<{ results: never[]; valid_sources: never[] }>((resolve) => {
      resolveProbe = resolve;
    });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe-batch") return pendingProbe;
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    const input = await screen.findByLabelText("视频链接或分享文本");
    fireEvent.change(input, {
      target: { value: "https://youtu.be/abc https://youtu.be/def" },
    });
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    const loadingPreview = await screen.findByRole("status", {
      name: "正在检查 2 个链接",
    });
    expect(loadingPreview).toHaveClass("launcher-signal");
    expect(loadingPreview).toHaveAttribute("data-launcher-signal", "probing");
    expect(screen.getByText("正在检查 2 个链接")).toBeInTheDocument();

    resolveProbe({ results: [], valid_sources: [] });
    expect(
      await screen.findByRole("heading", { name: "选择要处理的视频" }),
    ).toBeInTheDocument();
  });

  it("submits the complete pipeline when an authorized notes profile exists", async () => {
    let taskBody: unknown;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") {
        return { ...defaults, notes_profile_id: notesProfile.id };
      }
      if (path === "/api/profiles") return [notesProfile];
      if (path === "/api/sources/probe") return sourceProbe();
      if (path === "/api/tasks") {
        taskBody = options?.body;
        return { id: "11111111-1111-4111-8111-111111111111" };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await enterAndSubmit();

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/tasks",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(taskBody).toMatchObject({
      sources: [
        {
          kind: "url",
          locator: "https://www.bilibili.com/video/BV1x",
        },
      ],
      audio_export_enabled: true,
      output_type: "notes",
      local_asr_engine: "faster_whisper",
      translation_enabled: false,
      notes_enabled: true,
      notes_profile_id: "notes-profile",
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("applies summary settings to the current task", async () => {
    let taskBody: unknown;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") {
        return { ...defaults, notes_profile_id: notesProfile.id };
      }
      if (path === "/api/profiles") return [notesProfile];
      if (path === "/api/sources/probe") return sourceProbe();
      if (path === "/api/tasks") {
        taskBody = options?.body;
        return { id: "11111111-1111-4111-8111-111111111111" };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "总结设置" }),
    );
    expect(
      screen.getByRole("heading", { name: "总结设置" }),
    ).toBeInTheDocument();

    expect(
      screen.queryByRole("combobox", { name: "总结方式" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("combobox", { name: "输出语言" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "English" }));
    await userEvent.click(screen.getByRole("button", { name: "保存设置" }));

    await enterAndSubmit();

    await waitFor(() => expect(taskBody).toBeDefined());
    expect(taskBody).toMatchObject({
      output_type: "notes",
      notes_enabled: true,
      notes_profile_id: "notes-profile",
      notes_output_language: "en",
    });
    expect(taskBody).not.toHaveProperty("notes_template");
    expect(request).toHaveBeenCalledWith(
      "/api/tasks",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("moves a created link task into the global queue without a toast", async () => {
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [], nextCursor: null });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") return sourceProbe();
      if (path === "/api/tasks") return createdTask();
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <TaskQueueProvider>
          <CreateTaskPage />
          <CurrentPath />
        </TaskQueueProvider>
      </RouterProvider>,
    );

    await screen.findByRole("tab", { name: "链接" });
    await enterAndSubmit();

    expect(await screen.findByText("课程视频")).toBeInTheDocument();
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
    expect(screen.queryByLabelText("提示")).not.toBeInTheDocument();
  });

  it("keeps Shift+Enter newlines and submits complete share text with Enter", async () => {
    let probeBody: unknown;
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") {
        probeBody = options?.body;
        return sourceProbe();
      }
      if (path === "/api/tasks") {
        return { id: "11111111-1111-4111-8111-111111111111" };
      }
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    const user = userEvent.setup();
    const input = screen.getByLabelText("视频链接或分享文本");
    const shareText = "推荐课程\nhttps://b23.tv/AbC123。\n复制后打开";
    await user.type(input, "推荐课程");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(input, "https://b23.tv/AbC123。");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(input, "复制后打开");
    expect(input).toHaveValue(shareText);
    await user.keyboard("{Enter}");

    await waitFor(() => expect(probeBody).toEqual({ url: shareText }));
  });

  it("recognizes a collection and creates tasks only for selected videos", async () => {
    let batchBody: unknown;
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") return collectionProbe();
      if (path === "/api/tasks/batch") {
        batchBody = options?.body;
        return [];
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await userEvent.type(
      screen.getByLabelText("视频链接或分享文本"),
      "https://space.bilibili.com/2142762/lists/3662502?type=season",
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    expect(await screen.findByRole("heading", { name: "B站合集 · 前端课程" })).toBeInTheDocument();
    expect(screen.queryByText("已识别 B 站合集")).not.toBeInTheDocument();
    expect(screen.getByText("共 3 个视频，已选择 3 个")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /HTML 基础/u })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /CSS 布局/u })).toBeChecked();
    expect(screen.getByText("2:02")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "取消全选" }),
    ).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("checkbox", { name: /CSS 布局/u }));
    expect(
      screen.getByRole("button", { name: "全选视频" }),
    ).toHaveAttribute("aria-pressed", "mixed");
    await userEvent.click(
      screen.getByRole("button", { name: "开始解析 2 个视频" }),
    );

    await waitFor(() => expect(batchBody).toBeDefined());
    expect(batchBody).toMatchObject({
      sources: [
        {
          kind: "url",
          locator: "https://www.bilibili.com/video/BV1111111111",
        },
        {
          kind: "url",
          locator: "https://www.bilibili.com/video/BV3333333333",
        },
      ],
      output_type: "transcript",
      notes_enabled: false,
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("creates a 130-video collection in API-sized batches", async () => {
    const items = Array.from({ length: 130 }, (_, index) => ({
      id: `BV${String(index + 1).padStart(10, "0")}`,
      canonical_url: `https://www.bilibili.com/video/BV${String(index + 1).padStart(10, "0")}`,
      title: `Video ${index + 1}`,
      duration_ms: 60_000,
    }));
    const probe = collectionProbe();
    probe.collection = {
      ...probe.collection,
      total_items: items.length,
      items,
    };
    const batchBodies: Array<{ sources?: unknown[] }> = [];
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") return probe;
      if (path === "/api/tasks/batch") {
        batchBodies.push(options?.body as { sources?: unknown[] });
        return [];
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await userEvent.type(
      screen.getByLabelText("视频链接或分享文本"),
      "https://space.bilibili.com/2142762/lists/3662502?type=season",
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));
    await screen.findByText("共 130 个视频，已选择 130 个");
    await userEvent.click(
      screen.getByRole("button", { name: "开始解析 130 个视频" }),
    );

    await waitFor(() => expect(batchBodies).toHaveLength(2));
    expect(batchBodies[0].sources).toHaveLength(100);
    expect(batchBodies[1].sources).toHaveLength(30);
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("disables collection parsing after clearing the selection", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") return collectionProbe();
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await userEvent.type(
      screen.getByLabelText("视频链接或分享文本"),
      "https://space.bilibili.com/2142762/lists/3662502?type=season",
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));
    await screen.findByRole("heading", { name: "B站合集 · 前端课程" });
    await userEvent.click(
      screen.getByRole("button", { name: "取消全选" }),
    );

    expect(screen.getByText("请至少选择一个视频")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "全选视频" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("button", { name: "开始解析 0 个视频" }),
    ).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "全选视频" }));
    expect(screen.getByText("共 3 个视频，已选择 3 个")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消全选" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("still submits audio and transcript when notes are not configured", async () => {
    let taskBody: unknown;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") return sourceProbe();
      if (path === "/api/tasks") {
        taskBody = options?.body;
        return { id: "11111111-1111-4111-8111-111111111111" };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await enterAndSubmit();

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/tasks",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(taskBody).toMatchObject({
      audio_export_enabled: true,
      output_type: "transcript",
      translation_enabled: false,
      notes_enabled: false,
    });
    expect(taskBody).not.toHaveProperty("notes_profile_id");
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("explains when a platform requires verification cookies", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") {
        throw new ApiError(
          403,
          "auth_required",
          "platform requires authentication or fresh verification cookies",
        );
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await enterAndSubmit();

    expect(
      await screen.findByText(
        "该平台仍要求登录或验证 Cookie。请确认已在授权浏览器中登录；Windows Chrome/Edge 无法导入时请改用 Firefox，然后重启 VtNote。",
      ),
    ).toBeInTheDocument();
  });

  it("honors a transcript-only preference even when an AI profile is configured", async () => {
    savePreferences({
      defaultExportItems: ["transcript"],
      audioFormat: "m4a",
      subtitleFormat: "srt",
      noteFormat: "markdown",
    });
    let taskBody: unknown;
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults") {
        return { ...defaults, notes_profile_id: notesProfile.id };
      }
      if (path === "/api/profiles") return [notesProfile];
      if (path === "/api/sources/probe") return sourceProbe();
      if (path === "/api/tasks") {
        taskBody = options?.body;
        return { id: "11111111-1111-4111-8111-111111111111" };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "链接" });
    await enterAndSubmit();

    await waitFor(() => expect(taskBody).toBeDefined());
    expect(taskBody).toMatchObject({
      output_type: "transcript",
      audio_export_enabled: false,
      notes_enabled: false,
    });
    expect(taskBody).not.toHaveProperty("notes_profile_id");
  });

  it("uses the same transcript-only task options for a local media upload", async () => {
    savePreferences({
      defaultExportItems: ["transcript"],
      audioFormat: "m4a",
      subtitleFormat: "srt",
      noteFormat: "markdown",
    });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") {
        return { ...defaults, notes_profile_id: notesProfile.id };
      }
      if (path === "/api/profiles") return [notesProfile];
      throw new Error(`unexpected ${path}`);
    });
    const upload = vi.spyOn(api, "uploadTask").mockResolvedValue({
      id: "22222222-2222-4222-8222-222222222222",
    } as Task);

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "上传" });
    await userEvent.click(screen.getByRole("tab", { name: "上传" }));
    const media = new File(["media"], "sample.mp4", { type: "video/mp4" });
    await userEvent.upload(document.querySelector<HTMLInputElement>("input[type=file]")!, media);
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(upload).toHaveBeenCalled());
    expect(upload.mock.calls[0][1]).toMatchObject({
      kind: "media",
      output_type: "transcript",
      audio_export_enabled: false,
      notes_enabled: false,
    });
    expect(upload.mock.calls[0][1]).not.toHaveProperty("notes_profile_id");
  });

  it("uploads multiple local files as independent tasks", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      throw new Error(`unexpected ${path}`);
    });
    const upload = vi.spyOn(api, "uploadTask").mockResolvedValue({
      id: "44444444-4444-4444-8444-444444444444",
    } as Task);

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
        <CurrentPath />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "上传" });
    await userEvent.click(screen.getByRole("tab", { name: "上传" }));
    const media = new File(["media"], "sample.mp4", { type: "video/mp4" });
    const subtitle = new File(["字幕"], "sample.srt", {
      type: "application/x-subrip",
    });
    await userEvent.upload(
      document.querySelector<HTMLInputElement>("input[type=file]")!,
      [media, subtitle],
    );
    expect(screen.getByText("2 个文件")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(upload.mock.calls[0][1]).toMatchObject({ kind: "media" });
    expect(upload.mock.calls[1][1]).toMatchObject({
      kind: "subtitle",
      audio_export_enabled: false,
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
  });

  it("uploads a local subtitle without requesting ASR audio output", async () => {
    savePreferences({
      defaultExportItems: ["audio"],
      audioFormat: "m4a",
      subtitleFormat: "srt",
      noteFormat: "markdown",
    });
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/defaults") return defaults;
      if (path === "/api/profiles") return [];
      throw new Error(`unexpected ${path}`);
    });
    const upload = vi.spyOn(api, "uploadTask").mockResolvedValue({
      id: "33333333-3333-4333-8333-333333333333",
    } as Task);

    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    await screen.findByRole("tab", { name: "上传" });
    await userEvent.click(screen.getByRole("tab", { name: "上传" }));
    const subtitle = new File(["第一段\n第二段\n"], "captions.txt", {
      type: "text/plain",
    });
    await userEvent.upload(
      document.querySelector<HTMLInputElement>("input[type=file]")!,
      subtitle,
    );
    await userEvent.click(screen.getByRole("button", { name: "开始处理" }));

    await waitFor(() => expect(upload).toHaveBeenCalled());
    expect(upload.mock.calls[0][1]).toMatchObject({
      kind: "subtitle",
      output_type: "transcript",
      audio_export_enabled: false,
      notes_enabled: false,
    });
  });
});

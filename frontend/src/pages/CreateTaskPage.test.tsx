import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Task } from "../api/types";
import { savePreferences } from "../app/preferences";
import { RouterProvider, useRouter } from "../app/router";
import { CreateTaskPage } from "./CreateTaskPage";

const defaults = {
  asr_mode: "auto",
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
    source_kind: "bilibili",
    canonical_url: "https://www.bilibili.com/video/BV1x",
    title: "课程视频",
    duration_ms: 120000,
    subtitle_tracks: [],
  };
}

async function enterAndSubmit() {
  await userEvent.type(
    screen.getByLabelText("视频链接"),
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
  });

  it("shows only the compact link/upload launcher", async () => {
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
      screen.getByText("支持 B 站链接和本地音视频。"),
    ).toBeInTheDocument();
    expect(document.querySelector('label[for="source-url"]')).toHaveClass(
      "visually-hidden",
    );
    await userEvent.click(screen.getByRole("tab", { name: "上传" }));
    expect(screen.getByText("上传文件")).toBeInTheDocument();
    expect(screen.queryByText("从电脑中选择，不会显示本机路径")).not.toBeInTheDocument();
    expect(document.querySelector<HTMLInputElement>("input[type=file]")?.accept).toContain(
      ".aac",
    );
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByText("选择导出类型")).not.toBeInTheDocument();
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
      translation_enabled: false,
      notes_enabled: true,
      notes_profile_id: "notes-profile",
    });
    expect(screen.getByTestId("current-path")).toHaveTextContent("/tasks");
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
});

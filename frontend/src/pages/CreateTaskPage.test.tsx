import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api/client";
import { RouterProvider } from "../app/router";
import { CreateTaskPage } from "./CreateTaskPage";

describe("CreateTaskPage", () => {
  it("keeps one compact source composer and probes only after explicit action", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/readiness") {
        return {
          status: "partial",
          core: {},
          capabilities: { bilibili_url: true, youtube_url: true },
          local_model_state: "not_installed",
          limits: {
            max_task_sources: 1,
            max_media_bytes: 1000,
            max_subtitle_bytes: 100,
          },
        };
      }
      if (path === "/api/defaults") {
        return {
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
      }
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") {
        return {
          source_kind: "bilibili",
          canonical_url: "https://www.bilibili.com/video/BV1x",
          title: "课程视频",
          duration_ms: 120000,
          subtitle_tracks: [],
        };
      }
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    const url = await screen.findByLabelText("视频链接");
    await userEvent.type(url, "https://www.bilibili.com/video/BV1x");
    expect(request).not.toHaveBeenCalledWith(
      "/api/sources/probe",
      expect.anything(),
    );
    await userEvent.click(screen.getByRole("button", { name: "探测链接" }));
    await screen.findByText("课程视频");
    expect(screen.getByRole("checkbox", { name: "翻译" })).not.toBeChecked();
    expect(screen.getAllByRole("radio", { name: /公开|本地|字幕/ })).toHaveLength(
      3,
    );
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/api/sources/probe", {
        method: "POST",
        body: { url: "https://www.bilibili.com/video/BV1x" },
      }),
    );
    request.mockRestore();
  });

  it("shows probe failures before a source becomes ready", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/readiness") {
        return {
          status: "partial",
          core: {},
          capabilities: { bilibili_url: true, youtube_url: true },
          local_model_state: "not_installed",
          limits: {
            max_task_sources: 1,
            max_media_bytes: 1000,
            max_subtitle_bytes: 100,
          },
        };
      }
      if (path === "/api/defaults") {
        return {
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
      }
      if (path === "/api/profiles") return [];
      if (path === "/api/sources/probe") {
        throw new ApiError(
          400,
          "unsafe_source_url",
          "source host resolved to a proxy Fake-IP address",
        );
      }
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/">
        <CreateTaskPage />
      </RouterProvider>,
    );
    const url = await screen.findByLabelText("视频链接");
    await userEvent.type(url, "https://www.bilibili.com/video/BV1x");
    await userEvent.click(screen.getByRole("button", { name: "探测链接" }));
    await screen.findByText(
      "检测到代理 Fake-IP。请在代理软件中将该平台域名加入 Fake-IP 排除列表，然后重试。",
    );
    expect(screen.queryByText("处理方式")).not.toBeInTheDocument();
    request.mockRestore();
  });
});

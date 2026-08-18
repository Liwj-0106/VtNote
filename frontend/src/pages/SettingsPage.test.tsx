import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { loadPreferences } from "../app/preferences";
import { RouterProvider } from "../app/router";
import { SettingsPage } from "./SettingsPage";

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

const connections = [
  {
    id: "asr-connection",
    name: "腾讯云 ASR",
    protocol: "tencent_recording_asr",
    base_url: "https://asr.tencentcloudapi.com",
    parameters: {},
    has_secret: true,
    configured_fields: { secret_id: true, secret_key: true },
    revision: 1,
    tested: true,
    test_ok: true,
    test_message: "ok",
    cleanup_pending: false,
  },
];

const profiles = [
  {
    id: "asr-profile",
    name: "腾讯云 ASR",
    purpose: "cloud_asr",
    connection_id: "asr-connection",
    protocol: "tencent_recording_asr",
    base_url: "https://asr.tencentcloudapi.com",
    model: "16k_zh",
    context_length: 8192,
    options: {},
    revision: 1,
    tested: true,
    test_ok: true,
    test_message: "ok",
    upload_authorized: true,
    capability_fingerprint: {},
    chat_data_authorized: false,
  },
  {
    id: "notes-profile",
    name: "DeepSeek V4 Flash",
    purpose: "notes",
    connection_id: "notes-connection",
    protocol: "aliyun_bailian",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "deepseek-v4-flash",
    context_length: 32768,
    options: {},
    revision: 1,
    tested: true,
    test_ok: true,
    test_message: "ok",
    upload_authorized: false,
    capability_fingerprint: {},
    chat_data_authorized: true,
  },
];

describe("SettingsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("keeps only export, ASR, and model settings and persists their choices", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return profiles;
      if (path === "/api/connections") return connections;
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return { ...defaults, ...(options.body as object) };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings">
        <SettingsPage />
      </RouterProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "处理与导出" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "语音识别" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI 笔记" })).toBeInTheDocument();
    expect(
      screen.getByText("新任务只生成所选内容；未选择 AI 笔记时不会安排大模型总结。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("高级设置")).not.toBeInTheDocument();
    expect(screen.queryByText("本机数据")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
    expect(screen.queryByText("API Key 由服务端管理。")).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "管理 ASR" }),
    ).toHaveAttribute("href", "/settings/connections");
    expect(
      screen.getByRole("link", { name: "管理模型" }),
    ).toHaveAttribute("href", "/settings/ai-connections");
    expect(screen.getByRole("option", { name: "腾讯云 ASR" })).toHaveValue(
      "asr-profile",
    );

    await userEvent.click(screen.getByRole("checkbox", { name: "字幕原文" }));
    await userEvent.selectOptions(screen.getByLabelText("音频格式"), "mp3");
    expect(loadPreferences().defaultExportItems).toEqual(["audio", "notes"]);
    expect(loadPreferences().audioFormat).toBe("mp3");

    await userEvent.selectOptions(screen.getByLabelText("默认 ASR"), "asr-profile");
    await userEvent.selectOptions(screen.getByLabelText("默认模型"), "notes-profile");

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          asr_mode: "auto",
          cloud_asr_profile_id: "asr-profile",
        },
      });
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: { notes_enabled: true, notes_profile_id: "notes-profile" },
      });
    });
    expect(screen.getByRole("option", { name: "DeepSeek V4 Flash" })).toHaveValue(
      "notes-profile",
    );
  });

  it("shows a configured ASR connection name before verification", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/connections") {
        return [
          {
            ...connections[0],
            name: "我的腾讯云 ASR",
            tested: false,
            test_ok: null,
          },
        ];
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings">
        <SettingsPage />
      </RouterProvider>,
    );

    expect(
      await screen.findByRole("option", { name: "我的腾讯云 ASR" }),
    ).toHaveValue("connection:asr-connection");
    expect(screen.getByLabelText("默认 ASR")).toHaveValue(
      "connection:asr-connection",
    );
  });
});

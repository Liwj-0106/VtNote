import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ConnectionView, ProfileView } from "../api/types";
import { InterfacePreferencesProvider } from "../app/interfacePreferences";
import { RouterProvider } from "../app/router";
import { ModelSettingsPage } from "./ModelSettingsPage";

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

const defaultNotesPrompt = `基于提供的字幕证据生成一份面向普通读者的通用内容总结。先判断内容属于观点分享、教程、访谈、课程、评测、故事、新闻解读、会议或其他场景，再采用适合该场景的组织方式；不要机械输出场景判断。
1. 标题：用一行准确概括主题，不夸张、不使用空泛标题；
2. 摘要：用一至两段说明内容讲了什么、必要背景、主要脉络和核心结论，区分事实、观点与不确定表达；
3. 亮点：按重要性和逻辑顺序提炼四至八条可独立理解的信息，保留关键人物、机构、数字、日期、条件、因果、例外与明确行动项；可使用一个与内容直接相关的 emoji，但不要为了装饰堆砌；
4. 标签：内容主题明确时，在 key_points 中增加一个以“标签：”开头的条目，给出三至六个简短的 #主题标签；不要生成链接；
5. 思考：原文存在值得追问的原因、取舍或结论时，增加一至三个以“思考：”开头的条目，格式为“思考：问题｜回答：基于原文的简短回答”；没有充分依据时省略；
6. 术语：专业词、行业词或特定概念会影响理解时，增加以“术语：”开头的条目，格式为“术语：名称｜解释：基于原文的通俗解释”；常识词不解释；
7. 删除口头重复和无信息量表达，但不改变原意；只使用字幕证据，不补充外部知识，不猜测，证据不足时保留不确定性。`;

const connections: ConnectionView[] = [
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

const profiles: ProfileView[] = [
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

describe("ModelSettingsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  it("uses skeleton rows during the initial settings load", () => {
    vi.spyOn(api, "request").mockImplementation(
      () => new Promise(() => undefined),
    );

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    expect(
      screen.getByRole("status", { name: "语音模型 正在读取…" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "总结模型 正在读取…" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", { name: "默认语音模型" }),
    ).not.toBeInTheDocument();
  });

  it("adds a custom summary model from a dialog", async () => {
    const tokenHubConnection = {
      ...connections[0],
      id: "tokenhub-custom",
      name: "腾讯云 TokenHub",
      protocol: "tencent_tokenhub" as const,
      base_url: "https://tokenhub.tencentmaas.com/v1",
      configured_fields: { api_key: true },
      tested: false,
      test_ok: null,
    };
    let customProfile: ProfileView = {
      ...profiles[1],
      id: "custom-notes-profile",
      name: "腾讯云 TokenHub custom-model",
      connection_id: tokenHubConnection.id,
      protocol: "tencent_tokenhub" as const,
      model: "custom-model",
      tested: false,
      test_ok: null,
      chat_data_authorized: false,
    };
    let savedConnections: typeof connections = [];
    let savedProfiles: typeof profiles = [];
    let currentDefaults = defaults;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && !options?.method) return currentDefaults;
      if (path === "/api/profiles" && !options?.method) return savedProfiles;
      if (path === "/api/connections" && !options?.method) return savedConnections;
      if (path === "/api/connections" && options?.method === "POST") {
        savedConnections = [tokenHubConnection];
        return tokenHubConnection;
      }
      if (path === "/api/profiles" && options?.method === "POST") {
        savedProfiles = [customProfile];
        return customProfile;
      }
      if (path === `/api/profiles/${customProfile.id}/test`) {
        customProfile = { ...customProfile, tested: true, test_ok: true };
        savedProfiles = [customProfile];
        return customProfile;
      }
      if (path === `/api/profiles/${customProfile.id}/authorize-chat-data`) {
        customProfile = { ...customProfile, chat_data_authorized: true };
        savedProfiles = [customProfile];
        return customProfile;
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        currentDefaults = { ...currentDefaults, ...(options.body as object) };
        return currentDefaults;
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "添加模型" }),
    );
    const dialog = screen.getByRole("dialog", { name: "添加总结模型" });
    await userEvent.type(within(dialog).getByLabelText("API Key"), "sk-test");
    const modelInput = within(dialog).getByLabelText("模型 ID");
    await userEvent.clear(modelInput);
    await userEvent.type(modelInput, "custom-model");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "添加并启用" }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/connections", {
        method: "POST",
        body: {
          name: "腾讯云 TokenHub",
          protocol: "tencent_tokenhub",
          base_url: "https://tokenhub.tencentmaas.com/v1",
          parameters: {},
          credentials: { api_key: "sk-test" },
        },
      });
      expect(request).toHaveBeenCalledWith("/api/profiles", {
        method: "POST",
        body: expect.objectContaining({
          connection_id: "tokenhub-custom",
          model: "custom-model",
          purpose: "notes",
        }),
      });
      expect(request).toHaveBeenCalledWith(
        "/api/profiles/custom-notes-profile/test",
        expect.objectContaining({ method: "POST" }),
      );
      expect(request).toHaveBeenCalledWith(
        "/api/profiles/custom-notes-profile/authorize-chat-data",
        expect.objectContaining({ method: "POST" }),
      );
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          notes_enabled: true,
          notes_profile_id: "custom-notes-profile",
        },
      });
    });

    await userEvent.click(
      screen.getByRole("combobox", { name: "默认总结模型" }),
    );
    expect(
      screen.getByRole("option", { name: "腾讯云 TokenHub custom-model" }),
    ).toBeEnabled();
  });

  it("removes an incomplete model when verification fails", async () => {
    const tokenHubConnection: ConnectionView = {
      ...connections[0],
      id: "failed-connection",
      protocol: "tencent_tokenhub",
      configured_fields: { api_key: true },
    };
    const failedProfile: ProfileView = {
      ...profiles[1],
      id: "failed-profile",
      connection_id: tokenHubConnection.id,
      protocol: "tencent_tokenhub",
      model: "glm-5.1",
      tested: true,
      test_ok: false,
      chat_data_authorized: false,
    };
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles" && !options?.method) return [];
      if (path === "/api/connections" && !options?.method) return [];
      if (path === "/api/connections" && options?.method === "POST") {
        return tokenHubConnection;
      }
      if (path === "/api/profiles" && options?.method === "POST") {
        return failedProfile;
      }
      if (path === "/api/profiles/failed-profile/test") return failedProfile;
      if (
        path === "/api/connections/failed-connection?cascade_profiles=true" &&
        options?.method === "DELETE"
      ) {
        return { deleted: true };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "添加模型" }),
    );
    const dialog = screen.getByRole("dialog", { name: "添加总结模型" });
    await userEvent.type(within(dialog).getByLabelText("API Key"), "bad-key");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "添加并启用" }),
    );

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/connections/failed-connection?cascade_profiles=true",
        { method: "DELETE" },
      ),
    );
    expect(
      screen.getByRole("alert", {
        name: "验证失败，请检查凭据和模型 ID。",
      }),
    ).toBeInTheDocument();
  });

  it("adds Tencent ASR from a dialog with its required credential pair", async () => {
    let savedConnections: typeof connections = [];
    let savedProfiles: typeof profiles = [];
    let currentDefaults = {
      ...defaults,
      cloud_asr_profile_id: null as string | null,
    };
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && !options?.method) return currentDefaults;
      if (path === "/api/profiles") return savedProfiles;
      if (path === "/api/connections" && !options?.method) return savedConnections;
      if (path === "/api/connections" && options?.method === "POST") {
        savedConnections = [connections[0]];
        return connections[0];
      }
      if (path === "/api/connections/asr-connection/verify-asr") {
        savedProfiles = [profiles[0]];
        currentDefaults = {
          ...currentDefaults,
          asr_mode: "auto",
          cloud_asr_profile_id: profiles[0].id,
        };
        return profiles[0];
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "添加 ASR" }),
    );
    const dialog = screen.getByRole("dialog", { name: "添加腾讯云 ASR" });
    await userEvent.type(within(dialog).getByLabelText("SecretId"), "AKID-test");
    await userEvent.type(within(dialog).getByLabelText("SecretKey"), "secret-test");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "添加并启用" }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/connections", {
        method: "POST",
        body: expect.objectContaining({
          protocol: "tencent_recording_asr",
          credentials: {
            secret_id: "AKID-test",
            secret_key: "secret-test",
          },
        }),
      });
      expect(request).toHaveBeenCalledWith(
        "/api/connections/asr-connection/verify-asr",
        {
          method: "POST",
          body: {
            acknowledge_billable_request: true,
            authorize_task_audio_upload: true,
          },
        },
      );
    });
  });

  it("selects built-in and configured models from compact controls", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return profiles;
      if (path === "/api/connections") return connections;
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return { ...defaults, ...(options.body as object) };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "语音模型" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "总结模型" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("本地 ASR", { selector: ".preference-name" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "CPU 降级" })).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "说话人分离" }),
    ).not.toBeChecked();
    expect(screen.getByRole("textbox", { name: "提示词模板" })).toHaveValue(
      defaultNotesPrompt,
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: "默认语音模型" }),
    );
    expect(
      screen.getByRole("option", { name: "Faster-Whisper" }),
    ).toBeEnabled();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("link", { name: "管理" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加 ASR" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加模型" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "验证并启用" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("combobox", { name: "默认语音模型" }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: "Faster-Whisper" }),
    );
    expect(screen.getByRole("checkbox", { name: "CPU 降级" })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "说话人分离" }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("combobox", { name: "默认语音模型" }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: "腾讯云 ASR" }),
    );
    await userEvent.click(
      screen.getByRole("combobox", { name: "默认总结模型" }),
    );
    await userEvent.click(
      screen.getByRole("option", { name: "DeepSeek V4 Flash" }),
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "CPU 降级" }));
    await userEvent.click(
      screen.getByRole("checkbox", { name: "说话人分离" }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          asr_mode: "local",
          local_asr_engine: "faster_whisper",
          cloud_asr_profile_id: null,
        },
      });
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: { local_whisper_options: { cpu_fallback_enabled: true } },
      });
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          local_whisper_options: { speaker_diarization_enabled: true },
        },
      });
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: { asr_mode: "auto", cloud_asr_profile_id: "asr-profile" },
      });
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: { notes_enabled: true, notes_profile_id: "notes-profile" },
      });
    });
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("locks ASR selection while saving and rolls back after a failed save", async () => {
    let rejectSave: (reason?: unknown) => void = () => undefined;
    const pendingSave = new Promise((_, reject) => {
      rejectSave = reject;
    });
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return profiles;
      if (path === "/api/connections") return connections;
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return pendingSave;
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    const modelSelect = await screen.findByRole("combobox", {
      name: "默认语音模型",
    });
    await userEvent.click(modelSelect);
    await userEvent.click(
      screen.getByRole("option", { name: "Faster-Whisper" }),
    );
    expect(modelSelect).toBeDisabled();
    expect(modelSelect).toHaveTextContent("Faster-Whisper");
    await userEvent.click(modelSelect);
    expect(
      request.mock.calls.filter(
        ([path, options]) =>
          path === "/api/defaults" && options?.method === "PATCH",
      ),
    ).toHaveLength(1);

    rejectSave(new Error("save failed"));

    expect(await screen.findByText("设置未保存")).toBeInTheDocument();
    expect(modelSelect).toBeEnabled();
    expect(modelSelect).toHaveTextContent("自动");
    expect(screen.queryByText("设置加载失败")).not.toBeInTheDocument();
  });

  it("saves an edited prompt as the custom summary template", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return profiles;
      if (path === "/api/connections") return connections;
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return {
          ...defaults,
          notes_template: "custom",
          has_custom_prompt: true,
        };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    const prompt = await screen.findByRole("textbox", { name: "提示词模板" });
    await userEvent.clear(prompt);
    await userEvent.type(prompt, "只提炼可以立即执行的动作。{Tab}");

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          notes_template: "custom",
          notes_custom_prompt: "只提炼可以立即执行的动作。",
        },
      }),
    );
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("restores the current built-in prompt without a saved toast", async () => {
    let revealCount = 0;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) {
        return { ...defaults, notes_template: "custom", has_custom_prompt: true };
      }
      if (path === "/api/profiles") return profiles;
      if (path === "/api/connections") return connections;
      if (path === "/api/defaults/notes-prompt/reveal") {
        revealCount += 1;
        return revealCount === 1
          ? { prompt: "我的旧模板", is_custom: true }
          : { prompt: defaultNotesPrompt, is_custom: false };
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return { ...defaults, notes_template: "summary", has_custom_prompt: false };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "恢复默认" }));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: { notes_template: "summary", notes_custom_prompt: null },
      }),
    );
    expect(screen.getByRole("textbox", { name: "提示词模板" })).toHaveValue(
      defaultNotesPrompt,
    );
    expect(screen.queryByRole("button", { name: "恢复默认" })).not.toBeInTheDocument();
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("keeps an unverified ASR visible but unavailable", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/defaults" && !options?.method) return defaults;
      if (path === "/api/profiles") return [];
      if (path === "/api/connections") {
        return [{ ...connections[0], name: "我的 ASR", tested: false, test_ok: null }];
      }
      if (path === "/api/defaults/notes-prompt/reveal") {
        return { prompt: defaultNotesPrompt, is_custom: false };
      }
      throw new Error(`unexpected ${path}`);
    });

    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <ModelSettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(
      await screen.findByRole("combobox", { name: "默认语音模型" }),
    );
    expect(screen.getByRole("option", { name: "我的 ASR" })).toBeDisabled();
  });
});

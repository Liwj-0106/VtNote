import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../../api/client";
import type { ConnectionView, ProfileView } from "../../api/types";
import { InterfacePreferencesProvider } from "../../app/interfacePreferences";
import { InlineSummaryConnections } from "./InlineModelConnections";

describe("InlineSummaryConnections", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  it("offers mainstream providers and reveals custom Azure endpoint input", async () => {
    render(
      <InterfacePreferencesProvider>
        <InlineSummaryConnections
          connections={[]}
          onChanged={vi.fn(async () => undefined)}
        />
      </InterfacePreferencesProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = screen.getByRole("dialog", { name: "添加总结模型" });
    await userEvent.click(within(dialog).getByRole("combobox", { name: "服务商" }));
    for (const name of [
      "OpenAI",
      "Anthropic Claude",
      "Google Gemini",
      "OpenRouter",
      "DeepSeek",
      "硅基流动 SiliconFlow",
      "Azure OpenAI",
      "自定义兼容接口",
    ]) {
      expect(screen.getByRole("option", { name })).toBeEnabled();
    }

    await userEvent.click(screen.getByRole("option", { name: "Azure OpenAI" }));
    expect(within(dialog).getByRole("textbox", { name: "接口地址" })).toHaveValue("");
    expect(within(dialog).getByRole("textbox", { name: "模型 ID" })).toHaveValue("");
  });

  it("creates a custom OpenAI-compatible model from base URL, key, and model ID", async () => {
    const connection = {
      id: "custom-connection",
      name: "自定义模型",
    } as ConnectionView;
    const profile = {
      id: "custom-profile",
      name: "自定义模型 vendor/model",
      test_ok: false,
    } as ProfileView;
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/connections" && options?.method === "POST") {
        return connection;
      }
      if (path === "/api/profiles" && options?.method === "POST") {
        return profile;
      }
      if (path === "/api/profiles/custom-profile/test") {
        return { ...profile, tested: true, test_ok: true };
      }
      if (path === "/api/profiles/custom-profile/authorize-chat-data") {
        return { ...profile, tested: true, test_ok: true, chat_data_authorized: true };
      }
      if (path === "/api/defaults" && options?.method === "PATCH") {
        return {};
      }
      throw new Error(`unexpected ${path}`);
    });
    const onChanged = vi.fn(async () => undefined);

    render(
      <InterfacePreferencesProvider>
        <InlineSummaryConnections connections={[]} onChanged={onChanged} />
      </InterfacePreferencesProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = screen.getByRole("dialog", { name: "添加总结模型" });
    await userEvent.click(within(dialog).getByRole("combobox", { name: "服务商" }));
    await userEvent.click(screen.getByRole("option", { name: "自定义兼容接口" }));
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "接口地址" }),
      "https://api.example.com/v1",
    );
    await userEvent.type(within(dialog).getByLabelText("API Key"), "sk-test");
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "模型 ID" }),
      "vendor/model",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "添加并启用" }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/connections", {
        method: "POST",
        body: {
          name: "自定义模型",
          protocol: "openai_chat_completions",
          base_url: "https://api.example.com/v1",
          parameters: {},
          credentials: { api_key: "sk-test" },
        },
      });
      expect(request).toHaveBeenCalledWith("/api/profiles", {
        method: "POST",
        body: expect.objectContaining({
          connection_id: "custom-connection",
          model: "vendor/model",
          purpose: "notes",
        }),
      });
      expect(request).toHaveBeenCalledWith(
        "/api/profiles/custom-profile/authorize-chat-data",
        expect.objectContaining({ method: "POST" }),
      );
      expect(request).toHaveBeenCalledWith("/api/defaults", {
        method: "PATCH",
        body: {
          notes_enabled: true,
          notes_profile_id: "custom-profile",
        },
      });
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("shows an actionable bottom-right toast when the running backend is outdated", async () => {
    vi.spyOn(api, "request").mockRejectedValue(
      new ApiError(
        400,
        "invalid_configuration",
        "unsupported provider protocol",
      ),
    );

    render(
      <InterfacePreferencesProvider>
        <InlineSummaryConnections
          connections={[]}
          onChanged={vi.fn(async () => undefined)}
        />
      </InterfacePreferencesProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "添加模型" }));
    const dialog = screen.getByRole("dialog", { name: "添加总结模型" });
    await userEvent.click(within(dialog).getByRole("combobox", { name: "服务商" }));
    await userEvent.click(screen.getByRole("option", { name: "DeepSeek" }));
    await userEvent.type(within(dialog).getByLabelText("API Key"), "sk-test");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "添加并启用" }),
    );

    expect(
      await screen.findByRole("alert", {
        name: "当前服务尚未加载该模型协议，请重启 VtNote 后重试。",
      }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText("unsupported provider protocol"),
    ).not.toBeInTheDocument();
  });
});

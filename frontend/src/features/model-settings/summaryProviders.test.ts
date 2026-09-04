import { describe, expect, it } from "vitest";
import { SUMMARY_PROVIDER_PRESETS } from "./summaryProviders";

describe("summary provider presets", () => {
  it("covers native major APIs and broad OpenAI-compatible providers", () => {
    const ids = new Set(SUMMARY_PROVIDER_PRESETS.map((provider) => provider.id));
    expect(SUMMARY_PROVIDER_PRESETS.length).toBeGreaterThanOrEqual(25);
    for (const id of [
      "openai",
      "anthropic",
      "gemini",
      "azure-openai",
      "openrouter",
      "deepseek",
      "zhipu",
      "moonshot",
      "minimax",
      "siliconflow",
      "volcengine",
      "mistral",
      "groq",
      "xai",
      "together",
      "nvidia",
      "huggingface",
      "custom",
    ]) {
      expect(ids.has(id)).toBe(true);
    }
  });

  it("uses native protocols where authentication and payloads differ", () => {
    const protocol = (id: string) =>
      SUMMARY_PROVIDER_PRESETS.find((provider) => provider.id === id)?.protocol;

    expect(protocol("anthropic")).toBe("anthropic_messages");
    expect(protocol("gemini")).toBe("google_gemini");
    expect(protocol("azure-openai")).toBe("azure_openai");
    expect(protocol("openrouter")).toBe("openai_chat_completions");
  });

  it("keeps common providers two-field while allowing a custom compatible endpoint", () => {
    const preset = (id: string) =>
      SUMMARY_PROVIDER_PRESETS.find((provider) => provider.id === id);

    expect(preset("openai")).toMatchObject({
      protocol: "openai_chat_completions",
      baseUrl: "https://api.openai.com/v1",
    });
    expect(preset("openai")?.editableBaseUrl).toBeUndefined();
    expect(preset("deepseek")).toMatchObject({
      protocol: "openai_chat_completions",
      baseUrl: "https://api.deepseek.com",
      defaultModel: "deepseek-v4-flash",
    });
    expect(preset("custom")).toMatchObject({
      protocol: "openai_chat_completions",
      defaultModel: "",
      editableBaseUrl: true,
    });
  });

  it("keeps every fixed provider on a secure and runnable protocol contract", () => {
    const supportedProtocols = new Set([
      "aliyun_bailian",
      "tencent_tokenhub",
      "openai_chat_completions",
      "anthropic_messages",
      "google_gemini",
      "azure_openai",
    ]);
    const ids = new Set<string>();

    for (const provider of SUMMARY_PROVIDER_PRESETS) {
      expect(ids.has(provider.id)).toBe(false);
      ids.add(provider.id);
      expect(supportedProtocols.has(provider.protocol)).toBe(true);
      expect(provider.label.trim()).not.toBe("");
      expect(provider.connectionName.trim()).not.toBe("");
      if (provider.protocol !== "aliyun_bailian" && !provider.editableBaseUrl) {
        expect(provider.baseUrl?.startsWith("https://")).toBe(true);
      }
    }
  });
});

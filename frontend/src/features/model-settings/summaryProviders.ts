export type SummaryChatProtocol =
  | "aliyun_bailian"
  | "tencent_tokenhub"
  | "openai_chat_completions"
  | "anthropic_messages"
  | "google_gemini"
  | "azure_openai";

export interface SummaryProviderPreset {
  id: string;
  label: string;
  connectionName: string;
  protocol: SummaryChatProtocol;
  baseUrl?: string;
  defaultModel: string;
  workspaceRequired?: boolean;
  editableBaseUrl?: boolean;
}

export const SUMMARY_PROVIDER_PRESETS: SummaryProviderPreset[] = [
  {
    id: "tokenhub",
    label: "腾讯云 TokenHub",
    connectionName: "腾讯云 TokenHub",
    protocol: "tencent_tokenhub",
    baseUrl: "https://tokenhub.tencentmaas.com/v1",
    defaultModel: "glm-5.1",
  },
  {
    id: "bailian",
    label: "阿里云百炼",
    connectionName: "阿里云百炼",
    protocol: "aliyun_bailian",
    defaultModel: "qwen-plus",
    workspaceRequired: true,
  },
  {
    id: "openai",
    label: "OpenAI",
    connectionName: "OpenAI",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.openai.com/v1",
    defaultModel: "gpt-4.1-mini",
  },
  {
    id: "anthropic",
    label: "Anthropic Claude",
    connectionName: "Anthropic Claude",
    protocol: "anthropic_messages",
    baseUrl: "https://api.anthropic.com",
    defaultModel: "claude-sonnet-4-5",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    connectionName: "Google Gemini",
    protocol: "google_gemini",
    baseUrl: "https://generativelanguage.googleapis.com",
    defaultModel: "gemini-2.5-flash",
  },
  {
    id: "azure-openai",
    label: "Azure OpenAI",
    connectionName: "Azure OpenAI",
    protocol: "azure_openai",
    defaultModel: "",
    editableBaseUrl: true,
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    connectionName: "OpenRouter",
    protocol: "openai_chat_completions",
    baseUrl: "https://openrouter.ai/api/v1",
    defaultModel: "",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    connectionName: "DeepSeek",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.deepseek.com",
    defaultModel: "deepseek-v4-flash",
  },
  {
    id: "zhipu",
    label: "智谱 BigModel",
    connectionName: "智谱 BigModel",
    protocol: "openai_chat_completions",
    baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    defaultModel: "",
  },
  {
    id: "moonshot",
    label: "月之暗面 Kimi",
    connectionName: "月之暗面 Kimi",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.moonshot.cn/v1",
    defaultModel: "",
  },
  {
    id: "minimax",
    label: "MiniMax",
    connectionName: "MiniMax",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.minimaxi.com/v1",
    defaultModel: "",
  },
  {
    id: "siliconflow",
    label: "硅基流动 SiliconFlow",
    connectionName: "硅基流动",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.siliconflow.cn/v1",
    defaultModel: "",
  },
  {
    id: "volcengine",
    label: "火山引擎方舟",
    connectionName: "火山引擎方舟",
    protocol: "openai_chat_completions",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    defaultModel: "",
  },
  {
    id: "baidu",
    label: "百度千帆",
    connectionName: "百度千帆",
    protocol: "openai_chat_completions",
    baseUrl: "https://qianfan.baidubce.com/v2",
    defaultModel: "",
  },
  {
    id: "hunyuan",
    label: "腾讯混元",
    connectionName: "腾讯混元",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.hunyuan.cloud.tencent.com/v1",
    defaultModel: "",
  },
  {
    id: "mistral",
    label: "Mistral AI",
    connectionName: "Mistral AI",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.mistral.ai/v1",
    defaultModel: "",
  },
  {
    id: "groq",
    label: "Groq",
    connectionName: "Groq",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.groq.com/openai/v1",
    defaultModel: "",
  },
  {
    id: "xai",
    label: "xAI",
    connectionName: "xAI",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.x.ai/v1",
    defaultModel: "",
  },
  {
    id: "together",
    label: "Together AI",
    connectionName: "Together AI",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.together.xyz/v1",
    defaultModel: "",
  },
  {
    id: "fireworks",
    label: "Fireworks AI",
    connectionName: "Fireworks AI",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.fireworks.ai/inference/v1",
    defaultModel: "",
  },
  {
    id: "cerebras",
    label: "Cerebras",
    connectionName: "Cerebras",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.cerebras.ai/v1",
    defaultModel: "",
  },
  {
    id: "nvidia",
    label: "NVIDIA NIM",
    connectionName: "NVIDIA NIM",
    protocol: "openai_chat_completions",
    baseUrl: "https://integrate.api.nvidia.com/v1",
    defaultModel: "",
  },
  {
    id: "huggingface",
    label: "Hugging Face",
    connectionName: "Hugging Face",
    protocol: "openai_chat_completions",
    baseUrl: "https://router.huggingface.co/v1",
    defaultModel: "",
  },
  {
    id: "perplexity",
    label: "Perplexity",
    connectionName: "Perplexity",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.perplexity.ai",
    defaultModel: "",
  },
  {
    id: "github-models",
    label: "GitHub Models",
    connectionName: "GitHub Models",
    protocol: "openai_chat_completions",
    baseUrl: "https://models.github.ai/inference",
    defaultModel: "",
  },
  {
    id: "sambanova",
    label: "SambaNova",
    connectionName: "SambaNova",
    protocol: "openai_chat_completions",
    baseUrl: "https://api.sambanova.ai/v1",
    defaultModel: "",
  },
  {
    id: "custom",
    label: "自定义兼容接口",
    connectionName: "自定义模型",
    protocol: "openai_chat_completions",
    defaultModel: "",
    editableBaseUrl: true,
  },
];

export const SUMMARY_CHAT_PROTOCOLS = new Set<SummaryChatProtocol>(
  SUMMARY_PROVIDER_PRESETS.map((provider) => provider.protocol),
);

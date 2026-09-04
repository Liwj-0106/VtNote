import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export type InterfaceLanguage = "zh-CN" | "en";
export type ThemePreference = "light" | "dark" | "system";

export interface InterfacePreferences {
  language: InterfaceLanguage;
  theme: ThemePreference;
  accentColor: string | null;
  backgroundColor: string | null;
  foregroundColor: string | null;
}

const STORAGE_KEY = "vtnote.interface.v1";

export const DEFAULT_INTERFACE_PREFERENCES: InterfacePreferences = {
  language: "zh-CN",
  theme: "system",
  accentColor: null,
  backgroundColor: null,
  foregroundColor: null,
};

const HEX_COLOR_PATTERN = /^#[0-9a-f]{6}$/i;

export const DEFAULT_THEME_COLORS = {
  light: {
    accent: "#9f4b35",
    background: "#f5f4f1",
    foreground: "#1c1b19",
  },
  dark: {
    accent: "#d07a5b",
    background: "#171614",
    foreground: "#f4f1ea",
  },
} as const;

const messages = {
  "zh-CN": {
    "a11y.skipContent": "跳到主内容",
    "a11y.openNavigation": "打开导航",
    "a11y.closeNavigation": "关闭导航",
    "sidebar.navigation": "主导航",
    "sidebar.primaryPages": "主要页面",
    "sidebar.home": "VtNote 首页",
    "sidebar.expand": "展开侧边栏",
    "sidebar.collapse": "收起侧边栏",
    "sidebar.edgeExpand": "从边缘展开侧边栏",
    "sidebar.edgeCollapse": "从边缘收起侧边栏",
    "sidebar.newSummary": "新总结",
    "sidebar.library": "总结记录",
    "sidebar.collections": "合集管理",
    "sidebar.settings": "设置",
    "settings.title": "设置",
    "settings.navigation": "设置栏目",
    "settings.general": "通用",
    "settings.export": "导出",
    "settings.models": "模型",
    "general.description": "选择界面语言与显示方式，修改会立即生效。",
    "general.appearance": "外观",
    "general.appearanceDescription": "让 VtNote 适应你的工作环境。",
    "general.theme": "主题",
    "general.colors": "配色",
    "general.accentColor": "强调色",
    "general.backgroundColor": "背景",
    "general.foregroundColor": "前景",
    "general.resetColors": "恢复默认",
    "general.colorPicker": "色盘",
    "general.colorValue": "颜色值",
    "general.themeDescription": "选择浅色、深色，或自动跟随系统。",
    "theme.light": "白色",
    "theme.lightDescription": "明亮清晰，适合日间使用",
    "theme.dark": "深色",
    "theme.darkDescription": "降低亮度，适合夜间使用",
    "theme.system": "跟随系统",
    "theme.systemDescription": "随 Windows 外观自动切换",
    "general.language": "语言",
    "general.languageDescription": "选择设置与导航使用的界面语言。",
    "language.chinese": "中文",
    "language.chineseDescription": "简体中文",
    "language.english": "English",
    "language.englishDescription": "英文界面",
    "export.description": "设置默认导出内容、格式与保存位置。",
    "export.default": "默认导出",
    "export.audio": "音频",
    "export.transcript": "原文",
    "export.notes": "总结",
    "export.format": "导出格式",
    "export.audioFormat": "音频格式",
    "export.transcriptFormat": "原文格式",
    "export.notesFormat": "总结格式",
    "export.directory": "导出目录",
    "export.reading": "正在读取…",
    "export.restoreDefault": "默认",
    "export.choose": "选择",
    "export.choosing": "选择中…",
    "export.readError": "无法读取导出目录。",
    "export.chooseError": "无法选择导出目录。",
    "export.restoreError": "无法恢复默认目录。",
    "models.description": "设置语音识别与总结生成使用的模型。",
    "models.loadError": "设置加载失败",
    "models.saveError": "设置未保存",
    "models.speech": "语音模型",
    "models.default": "默认模型",
    "models.defaultSpeech": "默认语音模型",
    "models.auto": "自动",
    "models.localFree": "本地 ASR（免费）",
    "models.fasterWhisper": "Faster-Whisper",
    "models.senseVoice": "SenseVoice Small（快速）",
    "models.notInstalled": "未安装",
    "models.cpuFallback": "CPU 降级",
    "models.speakerDiarization": "说话人分离",
    "models.customAsr": "自定义 ASR",
    "models.add": "添加",
    "models.addAsr": "添加 ASR",
    "models.addModel": "添加模型",
    "models.addAndEnable": "添加并启用",
    "models.addAsrTitle": "添加腾讯云 ASR",
    "models.addModelTitle": "添加总结模型",
    "models.cancel": "取消",
    "models.adding": "添加中…",
    "models.provider": "服务商",
    "models.baseUrl": "接口地址",
    "models.modelId": "模型 ID",
    "models.verifyError": "验证失败，请检查凭据和模型 ID。",
    "models.summary": "总结模型",
    "models.defaultSummary": "默认总结模型",
    "models.notConfigured": "未配置",
    "models.customModel": "自定义模型",
    "models.promptTemplate": "提示词模板",
    "models.restoreDefault": "恢复默认",
  },
  en: {
    "a11y.skipContent": "Skip to main content",
    "a11y.openNavigation": "Open navigation",
    "a11y.closeNavigation": "Close navigation",
    "sidebar.navigation": "Main navigation",
    "sidebar.primaryPages": "Primary pages",
    "sidebar.home": "VtNote home",
    "sidebar.expand": "Expand sidebar",
    "sidebar.collapse": "Collapse sidebar",
    "sidebar.edgeExpand": "Expand sidebar from edge",
    "sidebar.edgeCollapse": "Collapse sidebar from edge",
    "sidebar.newSummary": "New summary",
    "sidebar.library": "Summary library",
    "sidebar.collections": "Collections",
    "sidebar.settings": "Settings",
    "settings.title": "Settings",
    "settings.navigation": "Settings sections",
    "settings.general": "General",
    "settings.export": "Export",
    "settings.models": "Models",
    "general.description": "Choose the interface language and appearance. Changes apply instantly.",
    "general.appearance": "Appearance",
    "general.appearanceDescription": "Make VtNote fit the way you work.",
    "general.theme": "Theme",
    "general.colors": "Colors",
    "general.accentColor": "Accent color",
    "general.backgroundColor": "Background",
    "general.foregroundColor": "Foreground",
    "general.resetColors": "Reset",
    "general.colorPicker": "color picker",
    "general.colorValue": "color value",
    "general.themeDescription": "Use a light or dark theme, or follow your system.",
    "theme.light": "Light",
    "theme.lightDescription": "Bright and clear for daytime use",
    "theme.dark": "Dark",
    "theme.darkDescription": "Lower brightness for evening use",
    "theme.system": "System",
    "theme.systemDescription": "Match the Windows appearance setting",
    "general.language": "Language",
    "general.languageDescription": "Choose the language used in settings and navigation.",
    "language.chinese": "中文",
    "language.chineseDescription": "Simplified Chinese",
    "language.english": "English",
    "language.englishDescription": "English interface",
    "export.description": "Set the default export contents, formats, and save location.",
    "export.default": "Default export",
    "export.audio": "Audio",
    "export.transcript": "Transcript",
    "export.notes": "Summary",
    "export.format": "Export formats",
    "export.audioFormat": "Audio format",
    "export.transcriptFormat": "Transcript format",
    "export.notesFormat": "Summary format",
    "export.directory": "Export folder",
    "export.reading": "Loading…",
    "export.restoreDefault": "Default",
    "export.choose": "Choose",
    "export.choosing": "Choosing…",
    "export.readError": "Could not load the export folder.",
    "export.chooseError": "Could not choose the export folder.",
    "export.restoreError": "Could not restore the default folder.",
    "models.description": "Choose the models used for speech recognition and summaries.",
    "models.loadError": "Could not load settings",
    "models.saveError": "Settings were not saved",
    "models.speech": "Speech model",
    "models.default": "Default model",
    "models.defaultSpeech": "Default speech model",
    "models.auto": "Automatic",
    "models.localFree": "Local ASR (free)",
    "models.fasterWhisper": "Faster-Whisper",
    "models.senseVoice": "SenseVoice Small (fast)",
    "models.notInstalled": "Not installed",
    "models.cpuFallback": "CPU fallback",
    "models.speakerDiarization": "Speaker separation",
    "models.customAsr": "Custom ASR",
    "models.add": "Add",
    "models.addAsr": "Add ASR",
    "models.addModel": "Add model",
    "models.addAndEnable": "Add and enable",
    "models.addAsrTitle": "Add Tencent ASR",
    "models.addModelTitle": "Add summary model",
    "models.cancel": "Cancel",
    "models.adding": "Adding…",
    "models.provider": "Provider",
    "models.baseUrl": "Endpoint",
    "models.modelId": "Model ID",
    "models.verifyError": "Verification failed. Check the credentials and model ID.",
    "models.summary": "Summary model",
    "models.defaultSummary": "Default summary model",
    "models.notConfigured": "Not configured",
    "models.customModel": "Custom model",
    "models.promptTemplate": "Prompt template",
    "models.restoreDefault": "Restore default",
  },
} as const;

export type MessageKey = keyof (typeof messages)["zh-CN"];

interface InterfacePreferencesValue extends InterfacePreferences {
  setLanguage: (language: InterfaceLanguage) => void;
  setTheme: (theme: ThemePreference) => void;
  setAccentColor: (accentColor: string | null) => void;
  setBackgroundColor: (backgroundColor: string | null) => void;
  setForegroundColor: (foregroundColor: string | null) => void;
  resetColors: () => void;
  text: (key: MessageKey) => string;
}

const InterfacePreferencesContext =
  createContext<InterfacePreferencesValue | null>(null);

export function loadInterfacePreferences(): InterfacePreferences {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null") as
      | Partial<InterfacePreferences>
      | null;
    return {
      language:
        stored?.language === "en" || stored?.language === "zh-CN"
          ? stored.language
          : DEFAULT_INTERFACE_PREFERENCES.language,
      theme:
        stored?.theme === "light" ||
        stored?.theme === "dark" ||
        stored?.theme === "system"
          ? stored.theme
          : DEFAULT_INTERFACE_PREFERENCES.theme,
      accentColor:
        typeof stored?.accentColor === "string" &&
        HEX_COLOR_PATTERN.test(stored.accentColor)
          ? stored.accentColor.toLowerCase()
          : DEFAULT_INTERFACE_PREFERENCES.accentColor,
      backgroundColor:
        typeof stored?.backgroundColor === "string" &&
        HEX_COLOR_PATTERN.test(stored.backgroundColor)
          ? stored.backgroundColor.toLowerCase()
          : DEFAULT_INTERFACE_PREFERENCES.backgroundColor,
      foregroundColor:
        typeof stored?.foregroundColor === "string" &&
        HEX_COLOR_PATTERN.test(stored.foregroundColor)
          ? stored.foregroundColor.toLowerCase()
          : DEFAULT_INTERFACE_PREFERENCES.foregroundColor,
    };
  } catch {
    return DEFAULT_INTERFACE_PREFERENCES;
  }
}

export function saveInterfacePreferences(
  preferences: InterfacePreferences,
): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
}

function systemUsesDarkTheme(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function accentContrastColor(accentColor: string): string {
  const channels = [1, 3, 5].map((offset) =>
    Number.parseInt(accentColor.slice(offset, offset + 2), 16) / 255,
  );
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4,
  );
  const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  return luminance > 0.179 ? "#181818" : "#ffffff";
}

export function applyInterfacePreferences(
  preferences: InterfacePreferences,
): void {
  const resolvedTheme =
    preferences.theme === "system"
      ? systemUsesDarkTheme()
        ? "dark"
        : "light"
      : preferences.theme;
  const root = document.documentElement;
  root.dataset.theme = resolvedTheme;
  root.dataset.themePreference = preferences.theme;
  root.lang = preferences.language;
  const defaultColors = DEFAULT_THEME_COLORS[resolvedTheme];
  const backgroundColor =
    preferences.backgroundColor ?? defaultColors.background;
  const foregroundColor =
    preferences.foregroundColor ?? defaultColors.foreground;
  if (preferences.backgroundColor || preferences.foregroundColor) {
    root.style.setProperty("--canvas", backgroundColor);
    root.style.setProperty(
      "--sidebar",
      `color-mix(in srgb, ${backgroundColor} 95%, ${foregroundColor})`,
    );
    root.style.setProperty(
      "--surface",
      `color-mix(in srgb, ${backgroundColor} 97%, #ffffff)`,
    );
    root.style.setProperty(
      "--surface-strong",
      `color-mix(in srgb, ${backgroundColor} 92%, #ffffff)`,
    );
    root.style.setProperty("--ink", foregroundColor);
    root.style.setProperty(
      "--ink-secondary",
      `color-mix(in srgb, ${foregroundColor} 66%, ${backgroundColor})`,
    );
    root.style.setProperty(
      "--ink-tertiary",
      `color-mix(in srgb, ${foregroundColor} 48%, ${backgroundColor})`,
    );
    root.style.setProperty(
      "--border",
      `color-mix(in srgb, ${foregroundColor} 16%, ${backgroundColor})`,
    );
    root.style.setProperty(
      "--border-strong",
      `color-mix(in srgb, ${foregroundColor} 28%, ${backgroundColor})`,
    );
    root.style.setProperty(
      "--hover",
      `color-mix(in srgb, ${foregroundColor} 7%, transparent)`,
    );
  } else {
    [
      "--canvas",
      "--sidebar",
      "--surface",
      "--surface-strong",
      "--ink",
      "--ink-secondary",
      "--ink-tertiary",
      "--border",
      "--border-strong",
      "--hover",
    ].forEach((property) => root.style.removeProperty(property));
  }
  if (preferences.accentColor) {
    root.style.setProperty("--accent", preferences.accentColor);
    root.style.setProperty(
      "--accent-contrast",
      accentContrastColor(preferences.accentColor),
    );
    root.style.setProperty("--focus", preferences.accentColor);
    root.style.setProperty("--terracotta", preferences.accentColor);
    root.style.setProperty(
      "--terracotta-strong",
      `color-mix(in srgb, ${preferences.accentColor} 82%, var(--ink))`,
    );
    root.style.setProperty(
      "--terracotta-soft",
      `color-mix(in srgb, ${preferences.accentColor} 14%, var(--surface))`,
    );
  } else {
    [
      "--accent",
      "--accent-contrast",
      "--focus",
      "--terracotta",
      "--terracotta-strong",
      "--terracotta-soft",
    ].forEach((property) => root.style.removeProperty(property));
  }
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", backgroundColor);
}

export function InterfacePreferencesProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [preferences, setPreferences] = useState(loadInterfacePreferences);

  useEffect(() => {
    applyInterfacePreferences(preferences);
    if (preferences.theme !== "system" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const applySystemTheme = () => applyInterfacePreferences(preferences);
    media.addEventListener?.("change", applySystemTheme);
    return () => media.removeEventListener?.("change", applySystemTheme);
  }, [preferences]);

  const updatePreferences = useCallback(
    (changes: Partial<InterfacePreferences>) => {
      const next = { ...preferences, ...changes };
      saveInterfacePreferences(next);
      applyInterfacePreferences(next);
      setPreferences(next);
    },
    [preferences],
  );

  const setLanguage = useCallback(
    (language: InterfaceLanguage) => updatePreferences({ language }),
    [updatePreferences],
  );
  const setTheme = useCallback(
    (theme: ThemePreference) => updatePreferences({ theme }),
    [updatePreferences],
  );
  const setAccentColor = useCallback(
    (accentColor: string | null) =>
      updatePreferences({
        accentColor:
          typeof accentColor === "string" && HEX_COLOR_PATTERN.test(accentColor)
            ? accentColor.toLowerCase()
            : null,
      }),
    [updatePreferences],
  );
  const setBackgroundColor = useCallback(
    (backgroundColor: string | null) =>
      updatePreferences({
        backgroundColor:
          typeof backgroundColor === "string" &&
          HEX_COLOR_PATTERN.test(backgroundColor)
            ? backgroundColor.toLowerCase()
            : null,
      }),
    [updatePreferences],
  );
  const setForegroundColor = useCallback(
    (foregroundColor: string | null) =>
      updatePreferences({
        foregroundColor:
          typeof foregroundColor === "string" &&
          HEX_COLOR_PATTERN.test(foregroundColor)
            ? foregroundColor.toLowerCase()
            : null,
      }),
    [updatePreferences],
  );
  const resetColors = useCallback(
    () =>
      updatePreferences({
        accentColor: null,
        backgroundColor: null,
        foregroundColor: null,
      }),
    [updatePreferences],
  );
  const text = useCallback(
    (key: MessageKey) => messages[preferences.language][key],
    [preferences.language],
  );
  const value = useMemo(
    () => ({
      ...preferences,
      resetColors,
      setAccentColor,
      setBackgroundColor,
      setForegroundColor,
      setLanguage,
      setTheme,
      text,
    }),
    [
      preferences,
      resetColors,
      setAccentColor,
      setBackgroundColor,
      setForegroundColor,
      setLanguage,
      setTheme,
      text,
    ],
  );

  return (
    <InterfacePreferencesContext.Provider value={value}>
      {children}
    </InterfacePreferencesContext.Provider>
  );
}

export function useInterfacePreferences(): InterfacePreferencesValue {
  const value = useContext(InterfacePreferencesContext);
  if (!value) throw new Error("InterfacePreferencesProvider is required");
  return value;
}

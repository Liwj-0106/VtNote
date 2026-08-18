import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnectionView, DefaultsView, ProfileView } from "../api/types";
import { AppLink } from "../app/router";
import {
  loadPreferences,
  savePreferences,
  type AppPreferences,
  type ExportItem,
} from "../app/preferences";

const exportItems: Array<{ value: ExportItem; label: string }> = [
  { value: "audio", label: "音频" },
  { value: "transcript", label: "字幕原文" },
  { value: "notes", label: "AI 笔记" },
];

export function SettingsPage() {
  const [preferences, setPreferences] = useState<AppPreferences>(loadPreferences);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [asrSelection, setAsrSelection] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.request<DefaultsView>("/api/defaults", { signal: controller.signal }),
      api.request<ProfileView[]>("/api/profiles", { signal: controller.signal }),
      api.request<ConnectionView[]>("/api/connections", {
        signal: controller.signal,
      }),
    ]).then(([nextDefaults, nextProfiles, nextConnections]) => {
      if (!controller.signal.aborted) {
        setDefaults(nextDefaults);
        setProfiles(nextProfiles);
        setConnections(nextConnections);
        const firstTencent = nextConnections.find(
          (connection) => connection.protocol === "tencent_recording_asr",
        );
        setAsrSelection(
          nextDefaults.cloud_asr_profile_id ??
            (firstTencent ? `connection:${firstTencent.id}` : ""),
        );
      }
    });
    return () => controller.abort();
  }, []);

  const markSaved = () => {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1600);
  };

  const updatePreference = <Key extends keyof AppPreferences>(
    key: Key,
    value: AppPreferences[Key],
  ) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    savePreferences(next);
    markSaved();
  };

  const toggleExportItem = (item: ExportItem) => {
    const selected = preferences.defaultExportItems.includes(item);
    if (selected && preferences.defaultExportItems.length === 1) return;
    updatePreference(
      "defaultExportItems",
      selected
        ? preferences.defaultExportItems.filter((value) => value !== item)
        : exportItems
            .map((option) => option.value)
            .filter(
              (value) =>
                value === item || preferences.defaultExportItems.includes(value),
            ),
    );
  };

  const patchDefaults = async (changes: Record<string, unknown>) => {
    const next = await api.request<DefaultsView>("/api/defaults", {
      method: "PATCH",
      body: changes,
    });
    setDefaults(next);
    markSaved();
  };

  const asrProfiles = profiles.filter(
    (profile) =>
      profile.purpose === "cloud_asr" &&
      profile.tested &&
      profile.test_ok === true &&
      profile.upload_authorized,
  );
  const asrOptions = connections
    .filter((connection) => connection.protocol === "tencent_recording_asr")
    .map((connection) => {
      const readyProfile = asrProfiles.find(
        (profile) => profile.connection_id === connection.id,
      );
      return {
        connection,
        profile: readyProfile,
        value: readyProfile?.id ?? `connection:${connection.id}`,
      };
    });
  const noteProfiles = profiles.filter(
    (profile) =>
      profile.purpose === "notes" &&
      profile.tested &&
      profile.test_ok === true &&
      profile.chat_data_authorized,
  );

  return (
    <div className="page settings-page compact-settings">
      <header className="page-header">
        <div>
          <h1>设置</h1>
        </div>
        <span className="settings-saved" role="status" aria-live="polite">
          {saved ? "已保存" : ""}
        </span>
      </header>

      <section className="settings-group" aria-labelledby="export-settings">
        <div className="settings-group-heading">
          <h2 id="export-settings">处理与导出</h2>
        </div>
        <div className="settings-fields">
          <fieldset className="field export-default-field">
            <legend className="field-label">默认生成与导出类型</legend>
            <div className="export-default-options">
              {exportItems.map((item) => (
                <label key={item.value}>
                  <input
                    type="checkbox"
                    checked={preferences.defaultExportItems.includes(item.value)}
                    disabled={
                      preferences.defaultExportItems.length === 1 &&
                      preferences.defaultExportItems.includes(item.value)
                    }
                    onChange={() => toggleExportItem(item.value)}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </div>
            <p className="field-hint">
              新任务只生成所选内容；未选择 AI 笔记时不会安排大模型总结。
            </p>
          </fieldset>
          <label className="field">
            <span className="field-label">音频格式</span>
            <select
              className="select-input"
              value={preferences.audioFormat}
              onChange={(event) =>
                updatePreference(
                  "audioFormat",
                  event.target.value as AppPreferences["audioFormat"],
                )
              }
            >
              <option value="m4a">M4A（推荐）</option>
              <option value="mp3">MP3</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">字幕格式</span>
            <select
              className="select-input"
              value={preferences.subtitleFormat}
              onChange={(event) =>
                updatePreference(
                  "subtitleFormat",
                  event.target.value as AppPreferences["subtitleFormat"],
                )
              }
            >
              <option value="srt">SRT</option>
              <option value="txt">纯文本 TXT</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">笔记格式</span>
            <select
              className="select-input"
              value={preferences.noteFormat}
              onChange={(event) =>
                updatePreference(
                  "noteFormat",
                  event.target.value as AppPreferences["noteFormat"],
                )
              }
            >
              <option value="markdown">Markdown</option>
              <option value="txt">纯文本 TXT</option>
            </select>
          </label>
        </div>
      </section>

      <section className="settings-group" aria-labelledby="asr-settings">
        <div className="settings-group-heading">
          <h2 id="asr-settings">语音识别</h2>
        </div>
        <div className="settings-fields">
          <label className="field">
            <span className="field-label">默认 ASR</span>
            <select
              className="select-input"
              value={asrSelection}
              onChange={(event) => {
                const value = event.target.value;
                setAsrSelection(value);
                if (value.startsWith("connection:")) return;
                void patchDefaults({
                  asr_mode: "auto",
                  cloud_asr_profile_id: value || null,
                });
              }}
            >
              <option value="">未配置</option>
              {asrOptions.map(({ connection, profile, value }) => (
                <option
                  key={connection.id}
                  value={value}
                  disabled={!profile}
                >
                  {connection.name}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span className="field-label">服务管理</span>
            <AppLink className="button" to="/settings/connections">
              管理 ASR
            </AppLink>
          </div>
        </div>
      </section>

      <section className="settings-group" aria-labelledby="model-settings">
        <div className="settings-group-heading">
          <h2 id="model-settings">AI 笔记</h2>
        </div>
        <div className="settings-fields">
          <label className="field">
            <span className="field-label">默认模型</span>
            <select
              className="select-input"
              value={defaults?.notes_profile_id ?? ""}
              onChange={(event) =>
                void patchDefaults({
                  notes_enabled: Boolean(event.target.value),
                  notes_profile_id: event.target.value || null,
                })
              }
            >
              <option value="">未配置</option>
              {noteProfiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.model === "glm-5.1"
                    ? "GLM-5.1"
                    : profile.model === "deepseek-v4-flash"
                      ? "DeepSeek V4 Flash"
                      : profile.name}
                </option>
              ))}
            </select>
          </label>
          <div className="field">
            <span className="field-label">模型管理</span>
            <AppLink className="button" to="/settings/ai-connections">
              管理模型
            </AppLink>
          </div>
        </div>
      </section>
    </div>
  );
}

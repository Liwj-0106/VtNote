import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  ConnectionView,
  DefaultsView,
  ProfileView,
  Readiness,
  StorageSummary,
} from "../api/types";
import { formatBytes } from "../app/format";
import { ArrowIcon } from "../app/icons";
import { AppLink } from "../app/router";
import { InlineNotice } from "../components/InlineNotice";

export function SettingsPage() {
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [storage, setStorage] = useState<StorageSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const [nextReadiness, nextConnections, nextProfiles, nextDefaults, nextStorage] =
        await Promise.all([
          api.request<Readiness>("/api/readiness"),
          api.request<ConnectionView[]>("/api/connections"),
          api.request<ProfileView[]>("/api/profiles"),
          api.request<DefaultsView>("/api/defaults"),
          api.request<StorageSummary>("/api/storage"),
        ]);
      setReadiness(nextReadiness);
      setConnections(nextConnections);
      setProfiles(nextProfiles);
      setDefaults(nextDefaults);
      setStorage(nextStorage);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "无法读取设置。",
      );
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const saveDefaults = async () => {
    if (!defaults) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await api.request<DefaultsView>("/api/defaults", {
        method: "PATCH",
        body: {
          asr_mode: defaults.asr_mode,
          cloud_asr_profile_id: defaults.cloud_asr_profile_id,
          translation_enabled: defaults.translation_enabled,
          translation_profile_id: defaults.translation_profile_id,
          translation_target_language: defaults.translation_target_language,
          notes_enabled: defaults.notes_enabled,
          notes_profile_id: defaults.notes_profile_id,
          notes_template: defaults.notes_template,
          notes_output_language: defaults.notes_output_language,
        },
      });
      setDefaults(updated);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "保存默认处理失败。",
      );
    } finally {
      setSaving(false);
    }
  };

  const cloudProfiles = profiles.filter(
    (profile) => profile.purpose === "cloud_asr",
  );
  const translationProfiles = profiles.filter(
    (profile) => profile.purpose === "translation",
  );
  const notesProfiles = profiles.filter(
    (profile) => profile.purpose === "notes",
  );
  const readyConnections = connections.filter(
    (connection) => connection.tested && connection.test_ok,
  ).length;

  return (
    <div className="page settings-page">
      <header className="page-header">
        <div>
          <p className="page-kicker">Preferences</p>
          <h1>设置</h1>
          <p className="page-intro">
            只展示会影响任务可用性、隐私或恢复的设置。
          </p>
        </div>
      </header>
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}

      <div className="settings-list">
        <AppLink className="settings-row settings-link" to="/setup">
          <div>
            <h2>运行环境</h2>
            <p>
              {readiness?.status === "ready"
                ? "全部能力可用"
                : readiness?.status === "blocked"
                  ? "核心能力需要修复"
                  : "可用，部分可选能力尚未配置"}
            </p>
          </div>
          <ArrowIcon />
        </AppLink>
        <AppLink
          className="settings-row settings-link"
          to="/settings/connections"
        >
          <div>
            <h2>腾讯云与百炼</h2>
            <p>
              {connections.length === 0
                ? "尚未配置"
                : `${connections.length} 个连接 · ${readyConnections} 个已验证`}
            </p>
          </div>
          <ArrowIcon />
        </AppLink>
        <AppLink
          className="settings-row settings-link"
          to="/settings/storage"
        >
          <div>
            <h2>存储与回收</h2>
            <p>
              {storage
                ? `临时文件 ${formatBytes(storage.active.size_bytes)} · 回收区 ${storage.trash.count} 项`
                : "正在读取占用"}
            </p>
          </div>
          <ArrowIcon />
        </AppLink>
      </div>

      {defaults && (
        <section className="defaults-section section-rule">
          <div className="section-heading-row">
            <div>
              <h2>默认处理</h2>
              <p>新任务可以临时覆盖这些选择。</p>
            </div>
          </div>
          <div className="defaults-form">
            <div className="field">
              <label className="field-label" htmlFor="default-asr">
                语音转录
              </label>
              <select
                id="default-asr"
                className="select-input"
                value={defaults.asr_mode}
                onChange={(event) =>
                  setDefaults({
                    ...defaults,
                    asr_mode: event.target.value as
                      | "auto"
                      | "cloud"
                      | "local",
                  })
                }
              >
                <option value="auto">自动路由</option>
                <option value="cloud">仅腾讯云</option>
                <option value="local">仅本地</option>
              </select>
            </div>
            {defaults.asr_mode !== "local" && (
              <div className="field">
                <label className="field-label" htmlFor="default-cloud">
                  腾讯云配置
                </label>
                <select
                  id="default-cloud"
                  className="select-input"
                  value={defaults.cloud_asr_profile_id ?? ""}
                  onChange={(event) =>
                    setDefaults({
                      ...defaults,
                      cloud_asr_profile_id: event.target.value || null,
                    })
                  }
                >
                  <option value="">没有可用配置时转本地</option>
                  {cloudProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <label className="switch-label defaults-switch">
              <input
                type="checkbox"
                checked={defaults.translation_enabled}
                onChange={(event) =>
                  setDefaults({
                    ...defaults,
                    translation_enabled: event.target.checked,
                  })
                }
              />
              翻译默认开启
            </label>
            {defaults.translation_enabled && (
              <div className="field">
                <label className="field-label" htmlFor="default-translation">
                  翻译配置
                </label>
                <select
                  id="default-translation"
                  className="select-input"
                  value={defaults.translation_profile_id ?? ""}
                  onChange={(event) =>
                    setDefaults({
                      ...defaults,
                      translation_profile_id: event.target.value || null,
                    })
                  }
                >
                  <option value="">选择配置</option>
                  {translationProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <label className="switch-label defaults-switch">
              <input
                type="checkbox"
                checked={defaults.notes_enabled}
                onChange={(event) =>
                  setDefaults({
                    ...defaults,
                    notes_enabled: event.target.checked,
                  })
                }
              />
              AI 笔记默认开启
            </label>
            {defaults.notes_enabled && (
              <div className="field">
                <label className="field-label" htmlFor="default-notes">
                  笔记配置
                </label>
                <select
                  id="default-notes"
                  className="select-input"
                  value={defaults.notes_profile_id ?? ""}
                  onChange={(event) =>
                    setDefaults({
                      ...defaults,
                      notes_profile_id: event.target.value || null,
                    })
                  }
                >
                  <option value="">选择配置</option>
                  {notesProfiles.map((profile) => (
                    <option key={profile.id} value={profile.id}>
                      {profile.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="actions defaults-actions">
              <button
                type="button"
                className="button button-primary"
                disabled={saving}
                onClick={() => void saveDefaults()}
              >
                {saving ? "正在保存…" : "保存默认处理"}
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

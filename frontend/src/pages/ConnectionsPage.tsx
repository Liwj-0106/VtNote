import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  ConnectionView,
  ModelStatus,
  ProfileView,
} from "../api/types";
import { formatBytes } from "../app/format";
import { AppLink } from "../app/router";
import { InlineNotice } from "../components/InlineNotice";
import { SecretField } from "../components/SecretField";

function formValue(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : "";
}

export function ConnectionsPage() {
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [testSamples, setTestSamples] = useState<Record<string, string>>({});
  const [billableAck, setBillableAck] = useState<Record<string, boolean>>({});
  const [dataAck, setDataAck] = useState<Record<string, boolean>>({});

  const load = async () => {
    try {
      const [nextConnections, nextProfiles, nextModel] = await Promise.all([
        api.request<ConnectionView[]>("/api/connections"),
        api.request<ProfileView[]>("/api/profiles"),
        api.request<ModelStatus>("/api/assets/local-whisper"),
      ]);
      setConnections(nextConnections);
      setProfiles(nextProfiles);
      setModel(nextModel);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "无法读取连接设置。",
      );
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const runAction = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(success);
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "操作失败，请重试。",
      );
    } finally {
      setBusy(null);
    }
  };

  const createTencent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const bucket = formValue(data, "cos_bucket");
    await runAction(
      "create-tencent",
      () =>
        api.request("/api/connections", {
          method: "POST",
          body: {
            name: formValue(data, "name"),
            protocol: "tencent_recording_asr",
            base_url: "https://asr.tencentcloudapi.com",
            parameters: {
              asr_region: "ap-guangzhou",
              cos_bucket: bucket,
              cos_region: "ap-guangzhou",
              cos_prefix: formValue(data, "cos_prefix") || "vtnote",
              cos_private: true,
              cos_configured: Boolean(bucket),
            },
            credentials: {
              secret_id: formValue(data, "secret_id"),
              secret_key: formValue(data, "secret_key"),
            },
          },
        }),
      "腾讯云连接已保存，密钥不会回显。",
    );
    form.reset();
  };

  const createBailian = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const workspaceId = formValue(data, "workspace_id").toLowerCase();
    await runAction(
      "create-bailian",
      () =>
        api.request("/api/connections", {
          method: "POST",
          body: {
            name: formValue(data, "name"),
            protocol: "aliyun_bailian",
            base_url: `https://${workspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`,
            parameters: { workspace_id: workspaceId },
            credentials: { api_key: formValue(data, "api_key") },
          },
        }),
      "百炼连接已保存，API Key 不会回显。",
    );
    form.reset();
  };

  const createProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const connectionId = formValue(data, "connection_id");
    const connection = connections.find((item) => item.id === connectionId);
    const purpose = formValue(data, "purpose");
    const tencent = connection?.protocol === "tencent_recording_asr";
    await runAction(
      "create-profile",
      () =>
        api.request("/api/profiles", {
          method: "POST",
          body: {
            name: formValue(data, "name"),
            purpose,
            connection_id: connectionId,
            model: tencent
              ? "16k_zh_en_2.0"
              : formValue(data, "model"),
            context_length: 32768,
            options: tencent
              ? {
                  language_scope: "zh_en_dialects",
                  res_text_format: 1,
                  sentence_max_length: 0,
                }
              : {
                  temperature: 0.1,
                  max_tokens: 4096,
                  enable_thinking: false,
                },
          },
        }),
      "处理配置已创建。请完成能力测试和数据授权。",
    );
    form.reset();
  };

  const testProfile = (profile: ProfileView) => {
    const tencent = profile.protocol === "tencent_recording_asr";
    return runAction(
      `test-${profile.id}`,
      () =>
        api.request(`/api/profiles/${profile.id}/test`, {
          method: "POST",
          body: tencent
            ? {
                test_kind: "provider_profile",
                acknowledge_billable_request: true,
                speech_sample_upload_id: testSamples[profile.id],
              }
            : {
                test_kind: "profile_capability_tested",
                acknowledge_billable_request: true,
              },
        }),
      "能力测试已完成。",
    );
  };

  const authorizeProfile = (profile: ProfileView) =>
    runAction(
      `authorize-${profile.id}`,
      () =>
        profile.purpose === "cloud_asr"
          ? api.request(`/api/profiles/${profile.id}/authorize-upload`, {
              method: "POST",
            })
          : api.request(`/api/profiles/${profile.id}/authorize-chat-data`, {
              method: "POST",
              body: { acknowledge_chat_data_upload: true },
            }),
      profile.purpose === "cloud_asr"
        ? "当前修订已授权上传音频。"
        : "当前修订已授权发送所列文本数据。",
    );

  return (
    <div className="page connections-page">
      <header className="page-header">
        <div>
          <AppLink className="back-link" to="/settings">
            返回设置
          </AppLink>
          <h1>腾讯云与百炼</h1>
          <p className="page-intro">
            只支持腾讯云录音文件识别与阿里云百炼北京工作空间。暂不接入国外 API 或中转站。
          </p>
        </div>
      </header>
      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {message && <InlineNotice tone="success">{message}</InlineNotice>}

      <section className="settings-section">
        <div className="section-heading-row">
          <div>
            <h2>连接</h2>
            <p>密钥由系统凭据存储保管；页面和接口只显示“已保存”。</p>
          </div>
        </div>
        {connections.length > 0 && (
          <div className="connection-list">
            {connections.map((connection) => (
              <article key={connection.id} className="connection-row">
                <div>
                  <h3>{connection.name}</h3>
                  <p>
                    {connection.protocol === "tencent_recording_asr"
                      ? "腾讯云 · 广州"
                      : "阿里云百炼 · 北京"}
                    {" · "}
                    {connection.has_secret ? "凭据已保存" : "凭据不完整"}
                  </p>
                </div>
                <div className="connection-actions">
                  <span
                    className={`readiness-state ${
                      connection.test_ok ? "is-ready" : "is-missing"
                    }`}
                  >
                    {connection.test_ok ? "连接已验证" : "等待验证"}
                  </span>
                  <button
                    type="button"
                    className="button"
                    disabled={busy !== null}
                    onClick={() =>
                      void runAction(
                        `connection-${connection.id}`,
                        () =>
                          api.request(
                            `/api/connections/${connection.id}/test`,
                            { method: "POST" },
                          ),
                        "连接策略验证完成。",
                      )
                    }
                  >
                    验证连接
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}

        <div className="provider-forms">
          <details>
            <summary>添加腾讯云 ASR 连接</summary>
            <form className="provider-form" onSubmit={createTencent}>
              <div className="field">
                <label className="field-label" htmlFor="tencent-name">
                  配置名称
                </label>
                <input
                  id="tencent-name"
                  name="name"
                  className="text-input"
                  required
                />
              </div>
              <SecretField
                id="tencent-secret-id"
                label="SecretId"
                name="secret_id"
                hasSecret={false}
              />
              <SecretField
                id="tencent-secret-key"
                label="SecretKey"
                name="secret_key"
                hasSecret={false}
              />
              <div className="field">
                <label className="field-label" htmlFor="cos-bucket">
                  私有 COS Bucket
                </label>
                <input
                  id="cos-bucket"
                  name="cos_bucket"
                  className="text-input"
                  placeholder="较大音频需要"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="cos-prefix">
                  对象前缀
                </label>
                <input
                  id="cos-prefix"
                  name="cos_prefix"
                  className="text-input"
                  defaultValue="vtnote"
                />
              </div>
              <p className="provider-fixed">
                固定地域 ap-guangzhou · 官方直连 endpoint · 私有 COS
              </p>
              <button
                className="button button-primary"
                type="submit"
                disabled={busy !== null}
              >
                保存腾讯云连接
              </button>
            </form>
          </details>
          <details>
            <summary>添加阿里云百炼连接</summary>
            <form className="provider-form" onSubmit={createBailian}>
              <div className="field">
                <label className="field-label" htmlFor="bailian-name">
                  配置名称
                </label>
                <input
                  id="bailian-name"
                  name="name"
                  className="text-input"
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="workspace-id">
                  北京工作空间 ID
                </label>
                <input
                  id="workspace-id"
                  name="workspace_id"
                  className="text-input mono"
                  pattern="[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                  required
                />
              </div>
              <SecretField
                id="bailian-api-key"
                label="API Key"
                name="api_key"
                hasSecret={false}
              />
              <p className="provider-fixed">
                只构造华北 2（北京）官方工作空间 endpoint，不接受自定义 Base URL。
                OpenAI-compatible 仅指接口协议，未调用 OpenAI。
              </p>
              <button
                className="button button-primary"
                type="submit"
                disabled={busy !== null}
              >
                保存百炼连接
              </button>
            </form>
          </details>
        </div>
      </section>

      <section className="settings-section section-rule">
        <div className="section-heading-row">
          <div>
            <h2>处理配置</h2>
            <p>测试和真实数据授权是两个独立步骤，连接修改后会自动失效。</p>
          </div>
        </div>
        {profiles.map((profile) => (
          <article key={profile.id} className="profile-row">
            <div className="profile-summary">
              <div>
                <h3>{profile.name}</h3>
                <p>
                  {profile.purpose === "cloud_asr"
                    ? "语音转录"
                    : profile.purpose === "translation"
                      ? "翻译"
                      : "AI 笔记"}
                  {" · "}
                  {profile.model}
                </p>
              </div>
              <span>
                {profile.test_ok
                  ? profile.purpose === "cloud_asr"
                    ? profile.upload_authorized
                      ? "已测试并授权"
                      : "等待上传授权"
                    : profile.chat_data_authorized
                      ? "已测试并授权"
                      : "等待文本授权"
                  : "等待能力测试"}
              </span>
            </div>
            {!profile.test_ok && (
              <div className="profile-action-block">
                {profile.protocol === "tencent_recording_asr" && (
                  <div className="field">
                    <label
                      className="field-label"
                      htmlFor={`sample-${profile.id}`}
                    >
                      已上传的短语音样本 ID
                    </label>
                    <input
                      id={`sample-${profile.id}`}
                      className="text-input mono"
                      value={testSamples[profile.id] ?? ""}
                      onChange={(event) =>
                        setTestSamples({
                          ...testSamples,
                          [profile.id]: event.target.value,
                        })
                      }
                    />
                  </div>
                )}
                <label className="rights-check">
                  <input
                    type="checkbox"
                    checked={billableAck[profile.id] ?? false}
                    onChange={(event) =>
                      setBillableAck({
                        ...billableAck,
                        [profile.id]: event.target.checked,
                      })
                    }
                  />
                  运行一次小型能力测试，可能产生少量费用。
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={
                    busy !== null ||
                    !billableAck[profile.id] ||
                    (profile.protocol === "tencent_recording_asr" &&
                      !testSamples[profile.id])
                  }
                  onClick={() => void testProfile(profile)}
                >
                  运行能力测试
                </button>
              </div>
            )}
            {profile.test_ok &&
              !(
                profile.purpose === "cloud_asr"
                  ? profile.upload_authorized
                  : profile.chat_data_authorized
              ) && (
                <div className="profile-action-block">
                  <p>
                    {profile.purpose === "cloud_asr"
                      ? "授权后，符合条件的任务可把转换后的音频上传到腾讯云，并可能计费。"
                      : "授权后，可发送字幕 cue、标题/元数据、目标语言和自定义提示词到百炼；不会发送音频。"}
                  </p>
                  <label className="rights-check">
                    <input
                      type="checkbox"
                      checked={dataAck[profile.id] ?? false}
                      onChange={(event) =>
                        setDataAck({
                          ...dataAck,
                          [profile.id]: event.target.checked,
                        })
                      }
                    />
                    我了解本次授权的数据范围，并授权当前配置修订。
                  </label>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={busy !== null || !dataAck[profile.id]}
                    onClick={() => void authorizeProfile(profile)}
                  >
                    确认授权
                  </button>
                </div>
              )}
          </article>
        ))}

        {connections.length > 0 && (
          <details className="profile-create">
            <summary>创建处理配置</summary>
            <form className="provider-form" onSubmit={createProfile}>
              <div className="field">
                <label className="field-label" htmlFor="profile-name">
                  配置名称
                </label>
                <input
                  id="profile-name"
                  name="name"
                  className="text-input"
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="profile-connection">
                  连接
                </label>
                <select
                  id="profile-connection"
                  name="connection_id"
                  className="select-input"
                  required
                >
                  <option value="">选择连接</option>
                  {connections.map((connection) => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="profile-purpose">
                  用途
                </label>
                <select
                  id="profile-purpose"
                  name="purpose"
                  className="select-input"
                  required
                >
                  <option value="cloud_asr">腾讯云语音转录</option>
                  <option value="translation">百炼翻译</option>
                  <option value="notes">百炼 AI 笔记</option>
                </select>
              </div>
              <div className="field">
                <label className="field-label" htmlFor="profile-model">
                  百炼模型名称
                </label>
                <input
                  id="profile-model"
                  name="model"
                  className="text-input mono"
                  placeholder="例如 qwen-plus；腾讯 ASR 会忽略此项"
                />
              </div>
              <button
                className="button button-primary"
                type="submit"
                disabled={busy !== null}
              >
                创建处理配置
              </button>
            </form>
          </details>
        )}
      </section>

      {model && (
        <section className="settings-section section-rule">
          <div className="section-heading-row">
            <div>
              <h2>本地 Whisper</h2>
              <p>
                {model.model_name} · 固定修订 ·{" "}
                {formatBytes(model.total_bytes)}
              </p>
            </div>
            <span className="readiness-state">
              {model.state === "installed" ? "已安装" : model.state}
            </span>
          </div>
          {["queued", "downloading", "verifying"].includes(model.state) ? (
            <div className="model-progress">
              <progress
                value={model.downloaded_bytes}
                max={model.total_bytes}
              />
              <span>
                {formatBytes(model.downloaded_bytes)} /{" "}
                {formatBytes(model.total_bytes)}
              </span>
              <button
                type="button"
                className="button"
                disabled={busy !== null}
                onClick={() =>
                  void runAction(
                    "cancel-model",
                    () =>
                      api.request("/api/assets/local-whisper/cancel", {
                        method: "POST",
                      }),
                    "已请求停止模型下载。",
                  )
                }
              >
                停止下载
              </button>
            </div>
          ) : model.state !== "installed" ? (
            <button
              type="button"
              className="button"
              disabled={busy !== null}
              onClick={() =>
                void runAction(
                  "install-model",
                  () =>
                    api.request("/api/assets/local-whisper/install", {
                      method: "POST",
                      body: {
                        acknowledge_download: true,
                        expected_revision: model.revision,
                      },
                    }),
                  "本地模型下载已加入队列。",
                )
              }
            >
              下载到 D 盘
            </button>
          ) : null}
        </section>
      )}
    </div>
  );
}

import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ConnectionView } from "../api/types";
import { AppLink } from "../app/router";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InlineNotice } from "../components/InlineNotice";
import { SecretField } from "../components/SecretField";

function formValue(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : "";
}

function publicError(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "操作失败，请重试。";
}

function nextConnectionName(connections: ConnectionView[]): string {
  const names = new Set(connections.map((connection) => connection.name));
  if (!names.has("腾讯云 ASR")) return "腾讯云 ASR";
  let suffix = 2;
  while (names.has(`腾讯云 ASR ${suffix}`)) suffix += 1;
  return `腾讯云 ASR ${suffix}`;
}

export function ConnectionsPage() {
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ConnectionView | null>(null);

  const load = async () => {
    try {
      const nextConnections = await api.request<ConnectionView[]>(
        "/api/connections",
      );
      setConnections(
        nextConnections.filter(
          (connection) => connection.protocol === "tencent_recording_asr",
        ),
      );
      setError(null);
    } catch (caught) {
      setError(publicError(caught));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const runAction = async (
    key: string,
    action: () => Promise<unknown>,
    success: string | null,
  ) => {
    setBusy(key);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(success);
      await load();
    } catch (caught) {
      setError(publicError(caught));
    } finally {
      setBusy(null);
    }
  };

  const createTencent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await runAction(
      "create-tencent",
      () =>
        api.request("/api/connections", {
          method: "POST",
          body: {
            name: nextConnectionName(connections),
            protocol: "tencent_recording_asr",
            base_url: "https://asr.tencentcloudapi.com",
            parameters: {
              asr_region: "ap-guangzhou",
              cos_configured: false,
            },
            credentials: {
              secret_id: formValue(data, "secret_id"),
              secret_key: formValue(data, "secret_key"),
            },
          },
        }),
      "ASR 已添加。",
    );
    form.reset();
  };

  const verifyConnection = async (connection: ConnectionView) => {
    setBusy(`verify-${connection.id}`);
    setError(null);
    setMessage(null);
    try {
      await api.request(`/api/connections/${connection.id}/verify-asr`, {
          method: "POST",
          body: {
            acknowledge_billable_request: true,
            authorize_task_audio_upload: true,
          },
        });
      await load();
    } catch {
      setConnections((current) =>
        current.map((item) =>
          item.id === connection.id
            ? { ...item, tested: true, test_ok: false }
            : item,
        ),
      );
    } finally {
      setBusy(null);
    }
  };

  const deleteConnection = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    await runAction(
      `delete-${target.id}`,
      () =>
        api.request(
          `/api/connections/${target.id}?cascade_profiles=true`,
          { method: "DELETE" },
        ),
      null,
    );
    setDeleteTarget(null);
  };

  return (
    <div className="page connections-page">
      <header className="page-header compact-page-header">
        <div>
          <AppLink className="back-link" to="/settings">
            返回设置
          </AppLink>
          <h1>ASR 配置</h1>
          <p className="page-intro">
            添加腾讯云密钥；验证时会用内置的 3 秒音频真实调用一次 ASR。
          </p>
        </div>
      </header>

      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {message && <InlineNotice tone="success">{message}</InlineNotice>}

      <section className="settings-section asr-add-section">
        <div className="section-heading-row">
          <div>
            <h2>添加 ASR</h2>
          </div>
        </div>
        <form className="asr-add-form" onSubmit={createTencent}>
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
          <button
            className="button button-primary asr-add-button"
            type="submit"
            disabled={busy !== null}
          >
            {busy === "create-tencent" ? "添加中…" : "添加"}
          </button>
        </form>
      </section>

      <section className="settings-section section-rule asr-saved-section">
        <div className="section-heading-row">
          <div>
            <h2>已添加的 ASR</h2>
          </div>
        </div>

        {connections.length === 0 ? (
          <p className="empty-copy">还没有添加 ASR。</p>
        ) : (
          <div className="connection-list">
            {connections.map((connection) => (
              <article key={connection.id} className="connection-row">
                <div className="connection-name">
                  <h3>{connection.name}</h3>
                  <span className={connection.test_ok ? "is-ready" : ""}>
                    {connection.test_ok
                      ? "验证通过"
                      : connection.tested
                        ? "验证失败"
                        : "未验证"}
                  </span>
                </div>
                <button
                  type="button"
                  className="button"
                  disabled={busy !== null}
                  onClick={() => void verifyConnection(connection)}
                >
                  {busy === `verify-${connection.id}` ? "验证中…" : "验证连接"}
                </button>
                <button
                  type="button"
                  className="button button-quiet connection-delete"
                  disabled={busy !== null}
                  onClick={() => setDeleteTarget(connection)}
                >
                  删除
                </button>
              </article>
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这个 ASR？"
        description="连接和对应的字幕配置会一起删除，历史任务不会受影响。"
        confirmLabel="确认删除"
        danger
        busy={deleteTarget ? busy === `delete-${deleteTarget.id}` : false}
        onConfirm={() => void deleteConnection()}
        onClose={() => {
          if (busy === null) setDeleteTarget(null);
        }}
      />
    </div>
  );
}

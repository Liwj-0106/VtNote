import { type FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ConnectionView } from "../api/types";
import { AppLink } from "../app/router";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { InlineNotice } from "../components/InlineNotice";
import { SecretField } from "../components/SecretField";

const TOKENHUB_BASE_URL = "https://tokenhub.tencentmaas.com/v1";

function formValue(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : "";
}

function publicError(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "操作失败，请重试。";
}

function nextConnectionName(connections: ConnectionView[]): string {
  const names = new Set(connections.map((connection) => connection.name));
  if (!names.has("腾讯云 TokenHub")) return "腾讯云 TokenHub";
  let suffix = 2;
  while (names.has(`腾讯云 TokenHub ${suffix}`)) suffix += 1;
  return `腾讯云 TokenHub ${suffix}`;
}

export function AiConnectionsPage() {
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
          (connection) => connection.protocol === "tencent_tokenhub",
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

  const createTokenHub = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await runAction(
      "create-tokenhub",
      () =>
        api.request("/api/connections", {
          method: "POST",
          body: {
            name: nextConnectionName(connections),
            protocol: "tencent_tokenhub",
            base_url: TOKENHUB_BASE_URL,
            parameters: {},
            credentials: {
              api_key: formValue(data, "api_key"),
            },
          },
        }),
      "AI 模型已添加。",
    );
    form.reset();
  };

  const verifyConnection = async (connection: ConnectionView) => {
    setBusy(`verify-${connection.id}`);
    setError(null);
    setMessage(null);
    try {
      await api.request(`/api/connections/${connection.id}/verify-chat`, {
        method: "POST",
        body: {
          acknowledge_billable_request: true,
          authorize_chat_data_upload: true,
        },
      });
      await load();
    } catch (caught) {
      setConnections((current) =>
        current.map((item) =>
          item.id === connection.id
            ? { ...item, tested: true, test_ok: false }
            : item,
        ),
      );
      setError(publicError(caught));
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
          <h1>AI 模型配置</h1>
          <p className="page-intro">
            添加 TokenHub API Key；验证时会用 GLM-5.1 真实发送一条最小测试文本。
          </p>
        </div>
      </header>

      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {message && <InlineNotice tone="success">{message}</InlineNotice>}

      <section className="settings-section ai-add-section">
        <div className="section-heading-row">
          <div>
            <h2>添加 AI 模型</h2>
          </div>
        </div>
        <form className="ai-add-form" onSubmit={createTokenHub}>
          <SecretField
            id="tokenhub-api-key"
            label="API Key"
            name="api_key"
            hasSecret={false}
          />
          <button
            className="button button-primary ai-add-button"
            type="submit"
            disabled={busy !== null}
          >
            {busy === "create-tokenhub" ? "添加中…" : "添加"}
          </button>
        </form>
      </section>

      <section className="settings-section section-rule asr-saved-section">
        <div className="section-heading-row">
          <div>
            <h2>已添加的 AI 模型</h2>
          </div>
        </div>

        {connections.length === 0 ? (
          <p className="empty-copy">还没有添加 AI 模型。</p>
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
        title="删除这个 AI 模型？"
        description="连接和对应的笔记配置会一起删除，历史任务不会受影响。"
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

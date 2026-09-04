import { type FormEvent, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { ConnectionView, ProfileView } from "../../api/types";
import { useInterfacePreferences } from "../../app/interfacePreferences";
import { FormDialog } from "../../components/FormDialog";
import { InlineNotice } from "../../components/InlineNotice";
import { MotionPresence } from "../../components/MotionPresence";
import { SecretField } from "../../components/SecretField";
import { SelectMenu } from "../../components/SelectMenu";
import { SUMMARY_PROVIDER_PRESETS } from "./summaryProviders";

const TOKENHUB_BASE_URL = "https://tokenhub.tencentmaas.com/v1";
const TENCENT_ASR_BASE_URL = "https://asr.tencentcloudapi.com";

function formValue(form: FormData, key: string): string {
  const value = form.get(key);
  return typeof value === "string" ? value.trim() : "";
}

function nextName(base: string, connections: ConnectionView[]): string {
  const names = new Set(connections.map((connection) => connection.name));
  if (!names.has(base)) return base;
  let suffix = 2;
  while (names.has(`${base} ${suffix}`)) suffix += 1;
  return `${base} ${suffix}`;
}

function publicError(caught: unknown, fallback: string): string {
  if (!(caught instanceof ApiError)) return fallback;
  const message = caught.message.toLowerCase();
  if (message.includes("unsupported provider protocol")) {
    return "当前服务尚未加载该模型协议，请重启 VtNote 后重试。";
  }
  if (message.includes("invalid provider credentials")) {
    return "API Key 无效，请检查后重试。";
  }
  return caught.message;
}

async function discardIncompleteConnection(connectionId: string) {
  try {
    await api.request(
      `/api/connections/${connectionId}?cascade_profiles=true`,
      { method: "DELETE" },
    );
  } catch {
    // Keep the original configuration error visible.
  }
}

export function InlineAsrConnections({
  connections,
  onChanged,
}: {
  connections: ConnectionView[];
  onChanged: () => Promise<void>;
}) {
  const { text } = useInterfacePreferences();
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createConnection = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    let createdConnection: ConnectionView | null = null;
    let configurationComplete = false;
    setBusy(true);
    setError(null);
    try {
      createdConnection = await api.request<ConnectionView>(
        "/api/connections",
        {
          method: "POST",
          body: {
            name: nextName("腾讯云 ASR", connections),
            protocol: "tencent_recording_asr",
            base_url: TENCENT_ASR_BASE_URL,
            parameters: {
              asr_region: "ap-guangzhou",
              cos_configured: false,
            },
            credentials: {
              secret_id: formValue(data, "secret_id"),
              secret_key: formValue(data, "secret_key"),
            },
          },
        },
      );
      await api.request(
        `/api/connections/${createdConnection.id}/verify-asr`,
        {
          method: "POST",
          body: {
            acknowledge_billable_request: true,
            authorize_task_audio_upload: true,
          },
        },
      );
      configurationComplete = true;
      form.reset();
      await onChanged();
      setAdding(false);
    } catch (caught) {
      if (createdConnection && !configurationComplete) {
        await discardIncompleteConnection(createdConnection.id);
      }
      setError(publicError(caught, text("models.verifyError")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-model-manager">
      <div className="inline-model-heading">
        <span className="preference-name">{text("models.customAsr")}</span>
        <button
          type="button"
          className="button button-quiet inline-model-toggle"
          onClick={() => {
            setError(null);
            setAdding(true);
          }}
        >
          {text("models.addAsr")}
        </button>
      </div>

      <FormDialog
        open={adding}
        title={text("models.addAsrTitle")}
        busy={busy}
        onClose={() => setAdding(false)}
      >
        <form className="settings-dialog-form" onSubmit={createConnection}>
          <SecretField
            id="inline-tencent-secret-id"
            label="SecretId"
            name="secret_id"
            hasSecret={false}
          />
          <SecretField
            id="inline-tencent-secret-key"
            label="SecretKey"
            name="secret_key"
            hasSecret={false}
          />
          <MotionPresence present={Boolean(error)}>
            {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
          </MotionPresence>
          <div className="actions dialog-actions">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => setAdding(false)}
            >
              {text("models.cancel")}
            </button>
            <button
              type="submit"
              className="button button-primary"
              disabled={busy}
            >
              {busy
                ? text("models.adding")
                : text("models.addAndEnable")}
            </button>
          </div>
        </form>
      </FormDialog>
    </div>
  );
}

export function InlineSummaryConnections({
  connections,
  onChanged,
}: {
  connections: ConnectionView[];
  onChanged: () => Promise<void>;
}) {
  const { text } = useInterfacePreferences();
  const [adding, setAdding] = useState(false);
  const [provider, setProvider] = useState("tokenhub");
  const [model, setModel] = useState("glm-5.1");
  const [baseUrl, setBaseUrl] = useState(TOKENHUB_BASE_URL);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedProvider =
    SUMMARY_PROVIDER_PRESETS.find((candidate) => candidate.id === provider) ??
    SUMMARY_PROVIDER_PRESETS[0];

  const changeProvider = (value: string) => {
    const next =
      SUMMARY_PROVIDER_PRESETS.find((candidate) => candidate.id === value) ??
      SUMMARY_PROVIDER_PRESETS[0];
    setProvider(next.id);
    setModel(next.defaultModel);
    setBaseUrl(next.baseUrl ?? "");
  };

  const createModel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    let createdConnection: ConnectionView | null = null;
    let configurationComplete = false;
    setBusy(true);
    setError(null);
    try {
      createdConnection = await api.request<ConnectionView>(
        "/api/connections",
        {
          method: "POST",
          body: {
            name: nextName(selectedProvider.connectionName, connections),
            protocol: selectedProvider.protocol,
            base_url:
              selectedProvider.protocol === "aliyun_bailian"
                ? undefined
                : baseUrl,
            parameters: selectedProvider.workspaceRequired
              ? { workspace_id: formValue(data, "workspace_id") }
              : {},
            credentials: { api_key: formValue(data, "api_key") },
          },
        },
      );
      const createdProfile = await api.request<ProfileView>("/api/profiles", {
        method: "POST",
        body: {
          name: `${createdConnection.name} ${model.trim()}`,
          purpose: "notes",
          connection_id: createdConnection.id,
          model: model.trim(),
          context_length: 32768,
          options: {
            temperature: 0.2,
            max_tokens: 4096,
            enable_thinking: false,
          },
        },
      });
      const tested = await api.request<ProfileView>(
        `/api/profiles/${createdProfile.id}/test`,
        {
          method: "POST",
          body: {
            test_kind: "profile_capability_tested",
            acknowledge_billable_request: true,
          },
        },
      );
      if (tested.test_ok !== true) throw new Error("verification failed");
      await api.request(
        `/api/profiles/${createdProfile.id}/authorize-chat-data`,
        {
          method: "POST",
          body: { acknowledge_chat_data_upload: true },
        },
      );
      await api.request("/api/defaults", {
        method: "PATCH",
        body: {
          notes_enabled: true,
          notes_profile_id: createdProfile.id,
        },
      });
      configurationComplete = true;
      form.reset();
      await onChanged();
      setAdding(false);
    } catch (caught) {
      if (createdConnection && !configurationComplete) {
        await discardIncompleteConnection(createdConnection.id);
      }
      setError(publicError(caught, text("models.verifyError")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-model-manager">
      <div className="inline-model-heading">
        <span className="preference-name">{text("models.customModel")}</span>
        <button
          type="button"
          className="button button-quiet inline-model-toggle"
          onClick={() => {
            setError(null);
            setAdding(true);
          }}
        >
          {text("models.addModel")}
        </button>
      </div>

      <FormDialog
        open={adding}
        title={text("models.addModelTitle")}
        busy={busy}
        onClose={() => setAdding(false)}
      >
        <form className="settings-dialog-form" onSubmit={createModel}>
          <div className="field">
            <label className="field-label" htmlFor="summary-provider">
              {text("models.provider")}
            </label>
            <SelectMenu
              id="summary-provider"
              ariaLabel={text("models.provider")}
              value={provider}
              onChange={changeProvider}
              options={SUMMARY_PROVIDER_PRESETS.map((candidate) => ({
                value: candidate.id,
                label: candidate.label,
              }))}
            />
          </div>
          {selectedProvider.workspaceRequired ? (
            <div className="field">
              <label className="field-label" htmlFor="bailian-workspace-id">
                Workspace ID
              </label>
              <input
                id="bailian-workspace-id"
                name="workspace_id"
                className="text-input"
                required
              />
            </div>
          ) : null}
          {selectedProvider.editableBaseUrl ? (
            <div className="field">
              <label className="field-label" htmlFor="summary-base-url">
                {text("models.baseUrl")}
              </label>
              <input
                id="summary-base-url"
                className="text-input"
                value={baseUrl}
                placeholder={
                  selectedProvider.protocol === "azure_openai"
                    ? "https://example.openai.azure.com/openai/v1"
                    : "https://api.example.com/v1"
                }
                required
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </div>
          ) : null}
          <SecretField
            id="inline-summary-api-key"
            label="API Key"
            name="api_key"
            hasSecret={false}
          />
          <div className="field">
            <label className="field-label" htmlFor="summary-model-id">
              {text("models.modelId")}
            </label>
            <input
              id="summary-model-id"
              className="text-input"
              value={model}
              required
              onChange={(event) => setModel(event.target.value)}
            />
          </div>
          <MotionPresence present={Boolean(error)}>
            {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
          </MotionPresence>
          <div className="actions dialog-actions">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => setAdding(false)}
            >
              {text("models.cancel")}
            </button>
            <button
              type="submit"
              className="button button-primary"
              disabled={
                busy ||
                !model.trim() ||
                (selectedProvider.editableBaseUrl && !baseUrl.trim())
              }
            >
              {busy
                ? text("models.adding")
                : text("models.addAndEnable")}
            </button>
          </div>
        </form>
      </FormDialog>
    </div>
  );
}

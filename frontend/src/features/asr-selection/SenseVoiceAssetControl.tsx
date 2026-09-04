import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { ModelStatus } from "../../api/types";

const ACTIVE_STATES = new Set(["queued", "downloading", "verifying"]);

function progress(status: ModelStatus | null): number {
  if (!status || status.total_bytes <= 0) return 0;
  return Math.min(1, status.downloaded_bytes / status.total_bytes);
}

export function SenseVoiceAssetControl() {
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [vad, setVad] = useState<ModelStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [nextModel, nextVad] = await Promise.all([
        api.request<ModelStatus>("/api/assets/sensevoice"),
        api.request<ModelStatus>("/api/assets/silero-vad"),
      ]);
      setModel(nextModel);
      setVad(nextVad);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const installing = Boolean(
    (model && ACTIVE_STATES.has(model.state)) ||
    (vad && ACTIVE_STATES.has(vad.state)),
  );
  const installed = model?.state === "installed" && vad?.state === "installed";
  const combinedProgress = useMemo(() => {
    if (!model || !vad) return 0;
    const total = model.total_bytes + vad.total_bytes;
    if (total <= 0) return 0;
    return Math.round(
      ((progress(model) * model.total_bytes + progress(vad) * vad.total_bytes) /
        total) *
        100,
    );
  }, [model, vad]);

  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [installing, refresh]);

  const install = async () => {
    if (!model || !vad || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const [nextModel, nextVad] = await Promise.all([
        api.request<ModelStatus>("/api/assets/sensevoice/install", {
          method: "POST",
          body: {
            acknowledge_download: true,
            expected_revision: model.revision,
          },
        }),
        api.request<ModelStatus>("/api/assets/silero-vad/install", {
          method: "POST",
          body: {
            acknowledge_download: true,
            expected_revision: vad.revision,
          },
        }),
      ]);
      setModel(nextModel);
      setVad(nextVad);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await Promise.allSettled([
        model && ACTIVE_STATES.has(model.state)
          ? api.request("/api/assets/sensevoice/cancel", { method: "POST" })
          : Promise.resolve(),
        vad && ACTIVE_STATES.has(vad.state)
          ? api.request("/api/assets/silero-vad/cancel", { method: "POST" })
          : Promise.resolve(),
      ]);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const state = failed
    ? "不可用"
    : installed
      ? "已安装"
      : installing
        ? `${combinedProgress}%`
        : model && vad
          ? "未安装"
          : "正在读取";

  return (
    <div className="preference-row">
      <span className="preference-name">SenseVoice Small INT8</span>
      <div className="preference-inline-actions" aria-live="polite">
        <span className="preference-value">{state}</span>
        {!installed && !installing ? (
          <button
            type="button"
            className="button button-secondary"
            disabled={busy || !model || !vad}
            onClick={() => void install()}
          >
            安装
          </button>
        ) : null}
        {installing ? (
          <button
            type="button"
            className="button button-quiet"
            disabled={busy}
            onClick={() => void cancel()}
          >
            取消
          </button>
        ) : null}
      </div>
    </div>
  );
}

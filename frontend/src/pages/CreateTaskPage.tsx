import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { DefaultsView, ProfileView, SourceProbe, Task } from "../api/types";
import { loadPreferences } from "../app/preferences";
import { useRouter } from "../app/router";
import { ArrowIcon } from "../app/icons";
import { FilePicker } from "../components/FilePicker";
import { InlineNotice } from "../components/InlineNotice";

type SourceMode = "url" | "media";

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "unsafe_source_url" && error.message.includes("proxy Fake-IP")) {
      return "检测到代理 Fake-IP。请在代理软件中排除该平台域名后重试。";
    }
    return error.message;
  }
  if (error instanceof DOMException && error.name === "AbortError") return "上传已取消";
  return "操作失败，请检查本地服务后重试。";
}

export function CreateTaskPage() {
  const { navigate } = useRouter();
  const [sourceMode, setSourceMode] = useState<SourceMode>("url");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{
    loaded: number;
    total: number;
  } | null>(null);
  const uploadController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      api.request<DefaultsView>("/api/defaults", { signal: controller.signal }),
      api.request<ProfileView[]>("/api/profiles", { signal: controller.signal }),
    ])
      .then(([nextDefaults, nextProfiles]) => {
        setDefaults(nextDefaults);
        setProfiles(nextProfiles);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const notesProfiles = profiles.filter(
    (profile) =>
      profile.purpose === "notes" &&
      profile.tested &&
      profile.test_ok === true &&
      profile.chat_data_authorized,
  );
  const notesProfileId =
    notesProfiles.find((profile) => profile.id === defaults?.notes_profile_id)?.id ??
    notesProfiles[0]?.id ??
    "";
  const sourceUrl = url.split(/\r?\n/u).map((line) => line.trim()).find(Boolean) ?? "";
  const sourceReady = sourceMode === "url" ? Boolean(sourceUrl) : file !== null;
  const canSubmit = sourceReady && !submitting && !loading;

  const taskOptions = () => {
    const selected = loadPreferences().defaultExportItems;
    const notesRequested = selected.includes("notes") && Boolean(notesProfileId);
    const outputType = notesRequested
      ? "notes"
      : selected.includes("transcript") || selected.includes("notes")
        ? "transcript"
        : "audio";
    return {
      output_type: outputType,
      audio_export_enabled: selected.includes("audio"),
      asr_mode: defaults?.asr_mode ?? "auto",
      translation_enabled: false,
      notes_enabled: notesRequested,
      ...(defaults?.cloud_asr_profile_id
        ? { cloud_asr_profile_id: defaults.cloud_asr_profile_id }
        : {}),
      ...(notesRequested
        ? {
            notes_profile_id: notesProfileId,
            notes_template: defaults?.notes_template ?? "summary",
            notes_output_language: defaults?.notes_output_language ?? "zh-Hans",
          }
        : {}),
    };
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    setUploadProgress(null);
    try {
      if (sourceMode === "url") {
        const probe = await api.request<SourceProbe>("/api/sources/probe", {
          method: "POST",
          body: { url: sourceUrl },
        });
        await api.request<Task>("/api/tasks", {
          method: "POST",
          body: {
            sources: [{ kind: "url", locator: probe.canonical_url }],
            ...taskOptions(),
          },
        });
      } else {
        const controller = new AbortController();
        uploadController.current = controller;
        await api.uploadTask<Task>(
          file!,
          { kind: "media", ...taskOptions() },
          {
            signal: controller.signal,
            onProgress: (loaded, total) => setUploadProgress({ loaded, total }),
          },
        );
      }
      navigate("/tasks", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      uploadController.current = null;
      setSubmitting(false);
    }
  };

  return (
    <div className="page launcher-page">
      <header className="launcher-heading">
        <h1>把音视频变成字幕和笔记</h1>
        <p>支持 B 站链接和本地音视频。</p>
      </header>

      <form className="launcher" onSubmit={submit}>
        <div className="launcher-tabs" role="tablist" aria-label="输入方式">
          <button
            type="button"
            role="tab"
            aria-selected={sourceMode === "url"}
            className={sourceMode === "url" ? "is-selected" : ""}
            onClick={() => setSourceMode("url")}
          >
            链接
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={sourceMode === "media"}
            className={sourceMode === "media" ? "is-selected" : ""}
            onClick={() => setSourceMode("media")}
          >
            上传
          </button>
        </div>

        <div className="launcher-input" data-source-mode={sourceMode}>
          {sourceMode === "url" ? (
            <>
              <label className="visually-hidden" htmlFor="source-url">
                视频链接
              </label>
              <input
                id="source-url"
                type="url"
                inputMode="url"
                value={url}
                placeholder="粘贴 B 站视频链接"
                onChange={(event) => {
                  setUrl(event.target.value);
                  setError(null);
                }}
              />
            </>
          ) : (
            <FilePicker
              id="source-file"
              accept=".mp4,.mkv,.mov,.webm,.avi,.m4v,.aac,.mp3,.m4a,.wav,.flac,.ogg,.opus"
              file={file}
              limitBytes={null}
              onChange={setFile}
            />
          )}
          <button
            className="launcher-submit"
            type="submit"
            aria-label="开始处理"
            disabled={!canSubmit}
          >
            <span>{submitting ? "正在处理" : "开始处理"}</span>
            {!submitting && <ArrowIcon />}
          </button>
        </div>

      </form>

      {error && <InlineNotice tone="danger">{error}</InlineNotice>}
      {uploadProgress && (
        <div className="upload-progress" aria-live="polite">
          <progress value={uploadProgress.loaded} max={uploadProgress.total} />
          <span>正在导入本地音视频…</span>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => uploadController.current?.abort()}
          >
            取消上传
          </button>
        </div>
      )}
    </div>
  );
}

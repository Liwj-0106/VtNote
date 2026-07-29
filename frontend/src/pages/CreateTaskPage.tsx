import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type {
  DefaultsView,
  ProfileView,
  Readiness,
  SourceProbe,
  Task,
} from "../api/types";
import { formatBytes, formatDuration, sourceLabel } from "../app/format";
import { useRouter } from "../app/router";
import { FilePicker } from "../components/FilePicker";
import { InlineNotice } from "../components/InlineNotice";
import { ProfileSelect } from "../components/ProfileSelect";

type SourceMode = "url" | "media" | "subtitle";

const sourceModes: Array<{
  value: SourceMode;
  label: string;
  description: string;
}> = [
  { value: "url", label: "公开视频链接", description: "Bilibili 或 YouTube" },
  { value: "media", label: "本地媒体", description: "视频或音频文件" },
  { value: "subtitle", label: "字幕文件", description: "SRT、VTT、ASS 或 JSON" },
];

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof DOMException && error.name === "AbortError") {
    return "上传已取消";
  }
  return "操作失败，请检查本地服务后重试。";
}

export function CreateTaskPage() {
  const { navigate } = useRouter();
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sourceMode, setSourceMode] = useState<SourceMode>("url");
  const [url, setUrl] = useState("");
  const [probedUrl, setProbedUrl] = useState("");
  const [probe, setProbe] = useState<SourceProbe | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [probing, setProbing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<{
    loaded: number;
    total: number;
  } | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [asrMode, setAsrMode] = useState<"auto" | "cloud" | "local">("auto");
  const [cloudProfileId, setCloudProfileId] = useState("");
  const [translationEnabled, setTranslationEnabled] = useState(false);
  const [translationProfileId, setTranslationProfileId] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("zh-Hans");
  const [notesEnabled, setNotesEnabled] = useState(false);
  const [notesProfileId, setNotesProfileId] = useState("");
  const [notesTemplate, setNotesTemplate] = useState<
    "summary" | "key_points" | "custom"
  >("summary");
  const [notesOutputLanguage, setNotesOutputLanguage] = useState("zh-Hans");
  const [customPrompt, setCustomPrompt] = useState("");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoadError(null);
    Promise.all([
      api.request<Readiness>("/api/readiness", { signal: controller.signal }),
      api.request<DefaultsView>("/api/defaults", { signal: controller.signal }),
      api.request<ProfileView[]>("/api/profiles", {
        signal: controller.signal,
      }),
    ])
      .then(([nextReadiness, nextDefaults, nextProfiles]) => {
        setReadiness(nextReadiness);
        setDefaults(nextDefaults);
        setProfiles(nextProfiles);
        setAsrMode(nextDefaults.asr_mode);
        setCloudProfileId(nextDefaults.cloud_asr_profile_id ?? "");
        setTranslationEnabled(nextDefaults.translation_enabled);
        setTranslationProfileId(nextDefaults.translation_profile_id ?? "");
        setTargetLanguage(nextDefaults.translation_target_language);
        setNotesEnabled(nextDefaults.notes_enabled);
        setNotesProfileId(nextDefaults.notes_profile_id ?? "");
        setNotesTemplate(nextDefaults.notes_template);
        setNotesOutputLanguage(nextDefaults.notes_output_language);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(errorMessage(error));
        }
      });
    return () => controller.abort();
  }, []);

  const visibleTracks = useMemo(
    () =>
      (probe?.subtitle_tracks ?? []).filter(
        (track) => !track.is_translated && !track.is_live_chat,
      ),
    [probe],
  );
  const sourceReady =
    sourceMode === "url"
      ? probe !== null && probedUrl === url.trim()
      : file !== null;
  const fileLimit =
    sourceMode === "media"
      ? readiness?.limits.max_media_bytes ?? null
      : readiness?.limits.max_subtitle_bytes ?? null;
  const fileTooLarge = Boolean(
    file && fileLimit !== null && file.size > fileLimit,
  );
  const cloudProfiles = profiles.filter(
    (profile) =>
      profile.purpose === "cloud_asr" &&
      profile.tested &&
      profile.test_ok === true &&
      profile.upload_authorized,
  );
  const cloudAvailable = cloudProfiles.length > 0;
  const selectedCloud = cloudProfiles.find(
    (profile) => profile.id === cloudProfileId,
  );
  const actualCloudRoute =
    sourceMode !== "subtitle" &&
    visibleTracks.length === 0 &&
    asrMode !== "local" &&
    selectedCloud !== undefined;
  const optionsValid =
    (asrMode !== "cloud" || Boolean(selectedCloud)) &&
    (!translationEnabled || Boolean(translationProfileId)) &&
    (!notesEnabled ||
      (Boolean(notesProfileId) &&
        (notesTemplate !== "custom" || customPrompt.trim().length > 0)));
  const canSubmit =
    sourceReady &&
    !fileTooLarge &&
    optionsValid &&
    rightsConfirmed &&
    !submitting &&
    readiness?.status !== "blocked";

  const handleProbe = async () => {
    if (!url.trim()) return;
    setProbing(true);
    setSubmitError(null);
    try {
      const result = await api.request<SourceProbe>("/api/sources/probe", {
        method: "POST",
        body: { url: url.trim() },
      });
      setProbe(result);
      setProbedUrl(url.trim());
    } catch (error) {
      setProbe(null);
      setSubmitError(errorMessage(error));
    } finally {
      setProbing(false);
    }
  };

  const buildOptions = () => ({
    asr_mode: sourceMode === "subtitle" ? "auto" : asrMode,
    ...(cloudProfileId ? { cloud_asr_profile_id: cloudProfileId } : {}),
    translation_enabled: translationEnabled,
    ...(translationEnabled
      ? {
          translation_profile_id: translationProfileId,
          translation_target_language: targetLanguage,
        }
      : {}),
    notes_enabled: notesEnabled,
    ...(notesEnabled
      ? {
          notes_profile_id: notesProfileId,
          notes_template: notesTemplate,
          notes_output_language: notesOutputLanguage,
          ...(notesTemplate === "custom"
            ? { notes_custom_prompt: customPrompt.trim() }
            : {}),
        }
      : {}),
  });

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    setUploadProgress(null);
    try {
      let created: Task;
      if (sourceMode === "url") {
        created = await api.request<Task>("/api/tasks", {
          method: "POST",
          body: {
            sources: [{ kind: "url", locator: probe!.canonical_url }],
            ...buildOptions(),
          },
        });
      } else {
        const controller = new AbortController();
        uploadController.current = controller;
        created = await api.uploadTask<Task>(
          file!,
          {
            kind: sourceMode === "media" ? "media" : "subtitle",
            ...buildOptions(),
          },
          {
            signal: controller.signal,
            onProgress: (loaded, total) =>
              setUploadProgress({ loaded, total }),
          },
        );
      }
      navigate(`/tasks/${created.id}`, { replace: true });
    } catch (error) {
      setSubmitError(errorMessage(error));
    } finally {
      uploadController.current = null;
      setSubmitting(false);
    }
  };

  return (
    <div className="page create-page">
      <header className="page-header">
        <div>
          <p className="page-kicker">VtNote workspace</p>
          <h1>把视频变成可继续处理的文字</h1>
          <p className="page-intro">
            从一个公开链接或本地文件开始。原始视频不会长期保存。
          </p>
        </div>
      </header>

      {loadError && (
        <InlineNotice tone="danger" title="无法读取本地配置">
          {loadError}
        </InlineNotice>
      )}

      <form onSubmit={handleSubmit}>
        <section className="source-composer" aria-labelledby="source-heading">
          <h2 id="source-heading" className="visually-hidden">
            选择来源
          </h2>
          <div className="segmented-control" role="radiogroup" aria-label="来源类型">
            {sourceModes.map((mode) => (
              <label
                key={mode.value}
                className={sourceMode === mode.value ? "is-selected" : ""}
              >
                <input
                  type="radio"
                  name="source-mode"
                  value={mode.value}
                  checked={sourceMode === mode.value}
                  aria-label={mode.label}
                  onChange={() => {
                    setSourceMode(mode.value);
                    setFile(null);
                    setSubmitError(null);
                  }}
                />
                <span>{mode.label}</span>
                <small>{mode.description}</small>
              </label>
            ))}
          </div>

          <div className="source-input-area">
            {sourceMode === "url" ? (
              <div className="field">
                <label className="field-label" htmlFor="source-url">
                  视频链接
                </label>
                <div className="input-action-row">
                  <input
                    id="source-url"
                    className="text-input"
                    type="url"
                    value={url}
                    placeholder="粘贴 Bilibili 或 YouTube 公开链接"
                    onChange={(event) => {
                      setUrl(event.target.value);
                      setProbe(null);
                    }}
                  />
                  <button
                    className="button"
                    type="button"
                    disabled={!url.trim() || probing}
                    onClick={handleProbe}
                  >
                    {probing ? "正在探测…" : "探测链接"}
                  </button>
                </div>
                <p className="field-hint">
                  仅支持公开可访问内容，不读取 Cookie，也不处理会员或 DRM 视频。
                </p>
              </div>
            ) : (
              <FilePicker
                id="source-file"
                accept={
                  sourceMode === "media"
                    ? ".mp4,.mkv,.mov,.webm,.avi,.m4v,.mp3,.m4a,.wav,.flac,.ogg,.opus"
                    : ".srt,.vtt,.ass,.json"
                }
                file={file}
                limitBytes={fileLimit}
                onChange={setFile}
              />
            )}
          </div>

          {probe && probedUrl === url.trim() && (
            <div className="probe-result" aria-live="polite">
              <div>
                <strong>{probe.title ?? "未命名视频"}</strong>
                <p>
                  {sourceLabel(probe.source_kind)} ·{" "}
                  {formatDuration(probe.duration_ms)}
                </p>
              </div>
              <span>
                {visibleTracks.length > 0
                  ? `${visibleTracks.length} 条可用字幕`
                  : "未发现可用字幕"}
              </span>
            </div>
          )}
        </section>

        {sourceReady && (
          <section
            className="processing-options section-rule"
            aria-labelledby="processing-heading"
          >
            <div className="section-heading-row">
              <div>
                <h2 id="processing-heading">处理方式</h2>
                <p>字幕文件会跳过语音转录；其他选项按需开启。</p>
              </div>
            </div>

            {sourceMode !== "subtitle" && (
              <div className="option-row">
                <div>
                  <label className="field-label" htmlFor="asr-mode">
                    语音转录
                  </label>
                  <p className="field-hint">
                    自动模式优先已授权的腾讯云，失败时转本地。
                  </p>
                </div>
                <select
                  id="asr-mode"
                  className="select-input compact-control"
                  value={asrMode}
                  onChange={(event) =>
                    setAsrMode(
                      event.target.value as "auto" | "cloud" | "local",
                    )
                  }
                >
                  <option value="auto">自动路由</option>
                  <option value="cloud" disabled={!cloudAvailable}>
                    仅腾讯云
                  </option>
                  <option value="local">仅本地</option>
                </select>
              </div>
            )}

            {sourceMode !== "subtitle" &&
              asrMode !== "local" &&
              cloudAvailable && (
                <ProfileSelect
                  id="cloud-profile"
                  label="腾讯云配置"
                  purpose="cloud_asr"
                  profiles={profiles}
                  value={cloudProfileId}
                  required={asrMode === "cloud"}
                  onChange={setCloudProfileId}
                />
              )}

            <div className="option-row">
              <div>
                <label className="switch-label" htmlFor="translation-enabled">
                  <input
                    id="translation-enabled"
                    type="checkbox"
                    checked={translationEnabled}
                    onChange={(event) =>
                      setTranslationEnabled(event.target.checked)
                    }
                  />
                  <span>翻译</span>
                </label>
                <p className="field-hint">默认关闭，保持原文与译文分别存储。</p>
              </div>
              <span className="option-state">
                {translationEnabled ? "已开启" : "关闭"}
              </span>
            </div>
            {translationEnabled && (
              <div className="nested-options">
                <ProfileSelect
                  id="translation-profile"
                  label="百炼翻译配置"
                  purpose="translation"
                  profiles={profiles}
                  value={translationProfileId}
                  required
                  onChange={setTranslationProfileId}
                />
                <div className="field">
                  <label className="field-label" htmlFor="target-language">
                    目标语言
                  </label>
                  <select
                    id="target-language"
                    className="select-input"
                    value={targetLanguage}
                    onChange={(event) => setTargetLanguage(event.target.value)}
                  >
                    <option value="zh-Hans">简体中文</option>
                    <option value="zh-Hant">繁体中文</option>
                    <option value="en">English</option>
                  </select>
                </div>
              </div>
            )}

            <div className="option-row">
              <div>
                <label className="switch-label" htmlFor="notes-enabled">
                  <input
                    id="notes-enabled"
                    type="checkbox"
                    checked={notesEnabled}
                    onChange={(event) => setNotesEnabled(event.target.checked)}
                  />
                  <span>AI 笔记</span>
                </label>
                <p className="field-hint">
                  直接读取原文并生成带时间点引用的中文笔记。
                </p>
              </div>
              <span className="option-state">
                {notesEnabled ? "已开启" : "关闭"}
              </span>
            </div>
            {notesEnabled && (
              <div className="nested-options">
                <ProfileSelect
                  id="notes-profile"
                  label="百炼笔记配置"
                  purpose="notes"
                  profiles={profiles}
                  value={notesProfileId}
                  required
                  onChange={setNotesProfileId}
                />
                <div className="field">
                  <label className="field-label" htmlFor="notes-template">
                    笔记形式
                  </label>
                  <select
                    id="notes-template"
                    className="select-input"
                    value={notesTemplate}
                    onChange={(event) =>
                      setNotesTemplate(
                        event.target.value as
                          | "summary"
                          | "key_points"
                          | "custom",
                      )
                    }
                  >
                    <option value="summary">综合总结</option>
                    <option value="key_points">干货提炼</option>
                    <option value="custom">自定义提示词</option>
                  </select>
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="notes-language">
                    输出语言
                  </label>
                  <select
                    id="notes-language"
                    className="select-input"
                    value={notesOutputLanguage}
                    onChange={(event) =>
                      setNotesOutputLanguage(event.target.value)
                    }
                  >
                    <option value="zh-Hans">简体中文</option>
                    <option value="zh-Hant">繁体中文</option>
                    <option value="en">English</option>
                  </select>
                </div>
                {notesTemplate === "custom" && (
                  <div className="field nested-full">
                    <label className="field-label" htmlFor="custom-prompt">
                      自定义提示词
                    </label>
                    <textarea
                      id="custom-prompt"
                      className="textarea-input"
                      value={customPrompt}
                      onChange={(event) => setCustomPrompt(event.target.value)}
                      placeholder="说明你希望笔记如何组织；请勿填写密钥。"
                    />
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {sourceReady && (
          <section className="submit-section section-rule">
            {actualCloudRoute && (
              <details className="cloud-disclosure">
                <summary>
                  将上传音频到腾讯云 · 可能计费 · 可改用仅本地
                </summary>
                <p>
                  音频会转换为 16 kHz 单声道后发送；配置为{" "}
                  {selectedCloud?.name}。临时媒体会进入 24 小时可恢复回收区。
                </p>
              </details>
            )}
            {(translationEnabled || notesEnabled) && (
              <p className="data-disclosure">
                百炼只接收所选处理需要的字幕文本、标题、语言和自定义提示词，不接收音频。
              </p>
            )}
            {!actualCloudRoute &&
              sourceMode !== "subtitle" &&
              asrMode !== "local" &&
              !cloudAvailable && (
                <p className="data-disclosure">
                  当前没有已测试并授权的腾讯云配置，本任务将使用本地转录。
                </p>
              )}
            <label className="rights-check">
              <input
                type="checkbox"
                checked={rightsConfirmed}
                onChange={(event) => setRightsConfirmed(event.target.checked)}
              />
              <span>
                我确认有权访问和处理该内容，并会遵守来源平台条款。
              </span>
            </label>
            {uploadProgress && (
              <div className="upload-progress" aria-live="polite">
                <progress
                  value={uploadProgress.loaded}
                  max={uploadProgress.total}
                />
                <span>
                  正在上传 {formatBytes(uploadProgress.loaded)} /{" "}
                  {formatBytes(uploadProgress.total)}
                </span>
                <button
                  type="button"
                  className="button button-quiet"
                  onClick={() => uploadController.current?.abort()}
                >
                  取消上传
                </button>
              </div>
            )}
            {submitError && (
              <InlineNotice tone="danger">{submitError}</InlineNotice>
            )}
            <div className="submit-actions">
              <button
                className="button button-primary create-submit"
                type="submit"
                disabled={!canSubmit}
              >
                {submitting ? "正在创建…" : "创建任务"}
              </button>
            </div>
          </section>
        )}
      </form>
      {!defaults && !loadError && (
        <p className="muted" role="status">
          正在读取本地配置…
        </p>
      )}
    </div>
  );
}

import type { Readiness } from "../api/types";
import { AppLink } from "../app/router";
import { useApiResource } from "../app/hooks";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import { SettingsRowsSkeleton } from "../components/Skeleton";

const checks = [
  {
    key: "database",
    group: "core",
    label: "任务数据库",
    description: "保存任务、阶段和恢复状态",
  },
  {
    key: "data_storage",
    group: "core",
    label: "长期存储",
    description: "保存字幕、译文和笔记",
  },
  {
    key: "runtime_storage",
    group: "core",
    label: "运行缓存",
    description: "临时音频和 24 小时回收区",
  },
  {
    key: "ffmpeg",
    group: "core",
    label: "FFmpeg",
    description: "读取和转换本地媒体",
  },
  {
    key: "bilibili_url",
    group: "capabilities",
    label: "Bilibili 公开链接",
    description: "探测公开页面和可用字幕",
  },
  {
    key: "youtube_url",
    group: "capabilities",
    label: "YouTube 公开链接",
    description: "需要固定版本 yt-dlp、EJS 和 Deno",
  },
  {
    key: "douyin_url",
    group: "capabilities",
    label: "抖音公开链接",
    description: "公开单视频；平台无字幕时进入语音识别",
  },
  {
    key: "local_asr",
    group: "capabilities",
    label: "本地语音转录",
    description: "SenseVoice 或 Faster-Whisper",
  },
] as const;

export function SetupPage() {
  const { data, error, loading, refresh } =
    useApiResource<Readiness>("/api/readiness");
  return (
    <div className="page setup-page">
      <header className="page-header">
        <div>
          <h1>运行环境</h1>
        </div>
        <button
          type="button"
          className="button"
          disabled={loading}
          onClick={() => void refresh()}
        >
          {loading && data ? "正在检查…" : "重新检查"}
        </button>
      </header>
      <MotionPresence present={Boolean(error)}>
        {error ? <InlineNotice tone="danger" title="检查失败">
          无法读取本地环境状态，请确认服务仍在运行。
        </InlineNotice> : null}
      </MotionPresence>
      <MotionPresence present={data?.status === "blocked"}>
        {data?.status === "blocked" ? <InlineNotice tone="danger" title="核心能力需要修复">
          处理任务前，请先修复下列标为“不可用”的核心项目。
        </InlineNotice> : null}
      </MotionPresence>
      <MotionPresence present={Boolean(data)}>
        {data ? (
        <div className="readiness-list">
          {checks.map((check) => {
            const available =
              check.group === "core"
                ? data.core[check.key]
                : data.capabilities[check.key];
            return (
              <section key={check.key} className="settings-row">
                <div>
                  <h2>{check.label}</h2>
                  <p>{check.description}</p>
                </div>
                <span
                  className={`readiness-state ${
                    available ? "is-ready" : "is-missing"
                  }`}
                >
                  <span aria-hidden="true" />
                  {available ? "可用" : "尚不可用"}
                </span>
              </section>
            );
          })}
          <section className="settings-row">
            <div>
              <h2>SenseVoice Small INT8</h2>
              <p>本地语音转录</p>
            </div>
            <span className="readiness-state">
              {data.local_asr_engines?.sensevoice_sherpa_onnx?.state === "installed"
                ? "已安装"
                : "尚未安装"}
            </span>
          </section>
        </div>
        ) : null}
      </MotionPresence>
      {loading && !data ? (
        <SettingsRowsSkeleton label="正在检查运行环境" count={9} />
      ) : null}
      <div className="setup-actions">
        <AppLink
          className={`button button-primary ${
            data?.status === "blocked" ? "is-disabled" : ""
          }`}
          to={data?.status === "blocked" ? "/setup" : "/"}
          aria-disabled={data?.status === "blocked" ? "true" : undefined}
        >
          继续使用可用功能
        </AppLink>
        <AppLink className="button button-quiet" to="/settings">
          查看设置
        </AppLink>
      </div>
    </div>
  );
}

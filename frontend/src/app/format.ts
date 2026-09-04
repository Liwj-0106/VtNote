const statusLabels: Record<string, string> = {
  queued: "处理中",
  running: "处理中",
  waiting_external: "处理中",
  cancel_requested: "处理中",
  canceled: "失败",
  completed: "完成",
  completed_with_warnings: "完成",
  failed: "失败",
};

const stageLabels: Record<string, string> = {
  source: "获取来源",
  transcribe: "生成转录",
  translate: "翻译",
  notes: "AI 笔记",
};

export function statusLabel(status: string): string {
  return statusLabels[status] ?? status;
}

export function stageLabel(stage: string): string {
  return stageLabels[stage] ?? stage;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined) return "—";
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatTimestamp(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const precision = index === 0 || value >= 10 ? 0 : 1;
  return `${value.toFixed(precision)} ${units[index]}`;
}

export function sourceLabel(kind: string): string {
  const labels: Record<string, string> = {
    bilibili: "Bilibili",
    douyin: "抖音",
    youtube: "YouTube",
    url: "公开视频",
    local_media: "本地媒体",
    local_subtitle: "字幕文件",
    uploaded_media: "本地媒体",
    uploaded_subtitle: "字幕文件",
  };
  return labels[kind] ?? kind;
}

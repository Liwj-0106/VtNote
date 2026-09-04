import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { StorageSummary, TrashAsset } from "../api/types";
import { formatBytes, formatDate } from "../app/format";
import { AppLink } from "../app/router";
import { EmptyState } from "../components/EmptyState";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import {
  SettingsRowsSkeleton,
  Skeleton,
  SkeletonStatus,
} from "../components/Skeleton";

export function StoragePage() {
  const [summary, setSummary] = useState<StorageSummary | null>(null);
  const [trash, setTrash] = useState<TrashAsset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async (showSkeleton = false) => {
    if (showSkeleton) setLoading(true);
    try {
      const [nextSummary, nextTrash] = await Promise.all([
        api.request<StorageSummary>("/api/storage"),
        api.request<TrashAsset[]>("/api/storage/trash"),
      ]);
      setSummary(nextSummary);
      setTrash(nextTrash);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "无法读取存储状态。",
      );
    } finally {
      if (showSkeleton) setLoading(false);
    }
  };
  useEffect(() => {
    void load(true);
  }, []);

  const restore = async (asset: TrashAsset) => {
    setRestoring(asset.id);
    setError(null);
    try {
      await api.request(`/api/storage/trash/${asset.id}/restore`, {
        method: "POST",
      });
      await load();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "恢复临时文件失败。",
      );
    } finally {
      setRestoring(null);
    }
  };

  return (
    <div className="page storage-page">
      <header className="page-header">
        <div>
          <AppLink className="back-link" to="/settings">
            返回设置
          </AppLink>
          <h1>存储与回收</h1>
          <p className="page-intro">
            字幕、译文和笔记长期保存；媒体只进入应用管理的临时区域。
          </p>
        </div>
      </header>
      <MotionPresence present={Boolean(error)}>
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
      </MotionPresence>

      {loading ? (
        <SettingsRowsSkeleton label="正在读取存储状态" count={3} />
      ) : null}
      <MotionPresence present={!loading && Boolean(summary)}>
        {summary ? (
        <div className="storage-summary">
          <section className="settings-row">
            <div>
              <h2>长期数据</h2>
              <p className="mono">{summary.data_root}</p>
            </div>
            <span>字幕与笔记</span>
          </section>
          <section className="settings-row">
            <div>
              <h2>运行缓存</h2>
              <p className="mono">{summary.runtime_cache_root}</p>
            </div>
            <span>
              {summary.active.count} 项 ·{" "}
              {formatBytes(summary.active.size_bytes)}
            </span>
          </section>
          <section className="settings-row">
            <div>
              <h2>可恢复回收区</h2>
              <p>保留 {summary.retention_hours} 小时后由维护任务清理。</p>
            </div>
            <span>
              {summary.trash.count} 项 ·{" "}
              {formatBytes(summary.trash.size_bytes)}
            </span>
          </section>
        </div>
        ) : null}
      </MotionPresence>

      <section className="settings-section section-rule">
        <div className="section-heading-row">
          <div>
            <h2>回收区</h2>
            <p>这里只能恢复 VtNote 自己创建并登记的临时媒体。</p>
          </div>
        </div>
        {loading ? (
          <StorageTrashSkeleton />
        ) : trash.length === 0 ? (
          <EmptyState
            title="回收区为空"
            description="处理完成或失败后的临时媒体会在这里短暂保留。"
          />
        ) : (
          <div className="trash-list">
            {trash.map((asset) => (
              <article key={asset.id} className="trash-row">
                <div>
                  <h3>{roleLabel(asset.role)}</h3>
                  <p>
                    {formatBytes(asset.size_bytes)} · 预计{" "}
                    {formatDate(asset.purge_after)} 后清理
                  </p>
                  <code>{asset.item_id.slice(0, 8)}</code>
                </div>
                <button
                  type="button"
                  className="button"
                  disabled={restoring !== null}
                  onClick={() => void restore(asset)}
                >
                  {restoring === asset.id ? "正在恢复…" : "恢复"}
                </button>
              </article>
            ))}
          </div>
        )}
        <InlineNotice tone="info">
          V1 不提供永久删除按钮。自动清理只作用于应用登记的临时文件，不会删除用户原始文件。
        </InlineNotice>
      </section>
    </div>
  );
}

function StorageTrashSkeleton() {
  return (
    <SkeletonStatus className="storage-trash-skeleton" label="正在读取回收区">
      {Array.from({ length: 2 }, (_, index) => (
        <div className="storage-trash-skeleton-row" key={index}>
          <div className="storage-trash-skeleton-copy">
            <Skeleton />
            <Skeleton />
            <Skeleton />
          </div>
          <Skeleton className="storage-trash-skeleton-action is-block" />
        </div>
      ))}
    </SkeletonStatus>
  );
}

function roleLabel(role: string): string {
  const labels: Record<string, string> = {
    uploaded_source: "上传的源文件",
    downloaded_audio: "下载的音频",
    cloud_audio: "云端转录音频",
    local_audio: "本地转录音频",
    failed_media: "失败任务媒体",
  };
  return labels[role] ?? "临时媒体";
}

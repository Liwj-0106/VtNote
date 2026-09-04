import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { SourceProbe } from "../../api/types";
import {
  ChevronDownIcon,
  PlayIcon,
} from "../../app/icons";
import { Skeleton, SkeletonStatus } from "../../components/Skeleton";

export interface EmbeddedSource {
  embedUrl: string;
  originalUrl: string;
  provider: "B 站" | "YouTube" | "抖音";
}

export interface VideoSeekRequest {
  startMs: number;
  revision: number;
}

const metadataCache = new Map<string, SourceProbe>();

export function formatPublishedAge(
  value: string,
  now = Date.now(),
): string | null {
  const timestamp = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(timestamp)) return null;
  const days = Math.max(0, Math.floor((now - timestamp) / 86_400_000));
  if (days < 1) return "今天";
  if (days < 30) return `${days} 天前`;
  if (days < 365) return `${Math.max(1, Math.floor(days / 30))} 个月前`;
  return `${Math.max(1, Math.floor(days / 365))} 年前`;
}

function secureUrl(locator: string): URL | null {
  try {
    const parsed = new URL(locator);
    return parsed.protocol === "https:" ? parsed : null;
  } catch {
    return null;
  }
}

export function resolveEmbeddedSource(
  sourceKind: string,
  locator: string,
): EmbeddedSource | null {
  if (!["url", "bilibili", "youtube", "douyin"].includes(sourceKind)) {
    return null;
  }
  const parsed = secureUrl(locator);
  if (!parsed) return null;
  const host = parsed.hostname.toLocaleLowerCase();

  if (host === "bilibili.com" || host === "www.bilibili.com") {
    const match = parsed.pathname.match(/^\/video\/(BV[0-9A-Za-z]{10}|av[0-9]+)\/?$/u);
    if (!match) return null;
    const videoId = match[1];
    const page = parsed.searchParams.get("p");
    const safePage = page && /^[1-9][0-9]{0,2}$/u.test(page) ? page : null;
    const parameters = new URLSearchParams({ autoplay: "0" });
    if (videoId.startsWith("BV")) parameters.set("bvid", videoId);
    else parameters.set("aid", videoId.slice(2));
    if (safePage) parameters.set("page", safePage);
    return {
      embedUrl: `https://player.bilibili.com/player.html?${parameters.toString()}`,
      originalUrl: `https://www.bilibili.com/video/${videoId}${safePage ? `?p=${safePage}` : ""}`,
      provider: "B 站",
    };
  }

  if (
    ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"].includes(host)
  ) {
    const candidate =
      host === "youtu.be"
        ? parsed.pathname.split("/").filter(Boolean)[0]
        : parsed.pathname === "/watch"
          ? parsed.searchParams.get("v")
          : parsed.pathname.match(/^\/(?:shorts|live)\/([0-9A-Za-z_-]{11})\/?$/u)?.[1];
    if (!candidate || !/^[0-9A-Za-z_-]{11}$/u.test(candidate)) return null;
    return {
      embedUrl: `https://www.youtube-nocookie.com/embed/${candidate}?autoplay=0&rel=0`,
      originalUrl: `https://www.youtube.com/watch?v=${candidate}`,
      provider: "YouTube",
    };
  }

  if (host === "douyin.com" || host === "www.douyin.com") {
    const videoId = parsed.pathname.match(/^\/video\/([0-9]{10,24})\/?$/u)?.[1];
    if (!videoId) return null;
    return {
      embedUrl: `https://open.douyin.com/player/video?vid=${videoId}&autoplay=0`,
      originalUrl: `https://www.douyin.com/video/${videoId}`,
      provider: "抖音",
    };
  }

  return null;
}

export function embeddedSourceAtTime(
  source: EmbeddedSource,
  startMs: number,
): string {
  const url = new URL(source.embedUrl);
  const seconds = Math.max(0, Math.floor(startMs / 1000));
  url.searchParams.set("autoplay", "1");
  if (source.provider === "YouTube") {
    url.searchParams.set("start", String(seconds));
  } else if (source.provider === "抖音") {
    url.searchParams.set("start_time", String(seconds));
  } else {
    url.searchParams.set("t", String(seconds));
  }
  return url.toString();
}

export function SourceVideoPanel({
  sourceKind,
  locator,
  title,
  seekRequest = null,
}: {
  sourceKind: string;
  locator: string;
  title: string;
  seekRequest?: VideoSeekRequest | null;
}) {
  const source = resolveEmbeddedSource(sourceKind, locator);
  const sourceUrl = source?.originalUrl ?? null;
  const playerUrl =
    source && seekRequest
      ? embeddedSourceAtTime(source, seekRequest.startMs)
      : source?.embedUrl ?? null;
  const [metadata, setMetadata] = useState<SourceProbe | null>(() =>
    sourceUrl ? metadataCache.get(sourceUrl) ?? null : null,
  );
  const [metadataResolved, setMetadataResolved] = useState(
    !sourceUrl || metadata !== null,
  );
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);

  useEffect(() => {
    setDescriptionExpanded(false);
    if (!sourceUrl) {
      setMetadata(null);
      setMetadataResolved(true);
      return;
    }
    const cached = metadataCache.get(sourceUrl);
    if (cached) {
      setMetadata(cached);
      setMetadataResolved(true);
      return;
    }
    setMetadataResolved(false);
    const controller = new AbortController();
    let active = true;
    void api
      .request<SourceProbe>("/api/sources/probe", {
        method: "POST",
        body: { url: sourceUrl },
        signal: controller.signal,
      })
      .then((result) => {
        if (!active || result.result_type !== "single") return;
        metadataCache.set(sourceUrl, result);
        setMetadata(result);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setMetadataResolved(true);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [sourceUrl]);

  const author = metadata?.author ?? source?.provider ?? "本地视频";
  const publishedAge = metadata?.published_at
    ? formatPublishedAge(metadata.published_at)
    : null;
  const description = metadata?.description?.trim() ?? null;

  return (
    <section className="source-video-column" aria-label="视频播放">
      {source ? (
        <div className="source-video-frame">
          <iframe
            key={`${source.embedUrl}-${seekRequest?.revision ?? 0}`}
            src={playerUrl ?? source.embedUrl}
            title={`${source.provider} 在线播放`}
            loading="eager"
            allow="autoplay; fullscreen; picture-in-picture"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"
          />
        </div>
      ) : (
        <div className="source-video-empty">
          <PlayIcon />
          <span>暂无视频预览</span>
        </div>
      )}
      <div className="source-video-meta" aria-busy={!metadataResolved}>
        <h2>
          {source ? (
            <a href={source.originalUrl} target="_blank" rel="noreferrer">
              {title}
            </a>
          ) : (
            title
          )}
        </h2>
        {!metadataResolved ? (
          <SkeletonStatus
            className="source-video-meta-skeleton"
            label="正在读取视频信息"
          >
            <div className="source-video-skeleton-byline" aria-hidden="true">
              <Skeleton className="source-video-skeleton-line is-author" />
              <Skeleton className="source-video-skeleton-line is-date" />
            </div>
            <Skeleton className="source-video-skeleton-line is-description-long" />
            <Skeleton className="source-video-skeleton-line is-description-short" />
          </SkeletonStatus>
        ) : (
          <>
            <div className="source-video-byline">
              <strong>{author}</strong>
              <span>
                {publishedAge ? `发布于 ${publishedAge}` : "发布时间未提供"}
              </span>
              {description && (
                <button
                  type="button"
                  aria-label={descriptionExpanded ? "收起简介" : "展开简介"}
                  aria-expanded={descriptionExpanded}
                  aria-controls="source-video-description"
                  onClick={() => setDescriptionExpanded((current) => !current)}
                >
                  <ChevronDownIcon />
                </button>
              )}
            </div>
            {description && (
              <p
                id="source-video-description"
                className={`source-video-description${descriptionExpanded ? " is-expanded" : ""}`}
                tabIndex={descriptionExpanded ? 0 : undefined}
              >
                {description}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import {
  embeddedSourceAtTime,
  formatPublishedAge,
  resolveEmbeddedSource,
  SourceVideoPanel,
} from "./SourceVideoPanel";

describe("resolveEmbeddedSource", () => {
  it("builds provider-owned players from sanitized public video ids", () => {
    expect(
      resolveEmbeddedSource(
        "bilibili",
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2&token=ignored",
      ),
    ).toMatchObject({
      embedUrl:
        "https://player.bilibili.com/player.html?autoplay=0&bvid=BV1xx411c7mD&page=2",
      originalUrl: "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
    });
    expect(
      resolveEmbeddedSource(
        "youtube",
        "https://www.youtube.com/watch?v=abcDEF12345&token=ignored",
      )?.embedUrl,
    ).toBe("https://www.youtube-nocookie.com/embed/abcDEF12345?autoplay=0&rel=0");
    expect(
      resolveEmbeddedSource(
        "douyin",
        "https://www.douyin.com/video/7531234567890123456?token=ignored",
      )?.embedUrl,
    ).toBe(
      "https://open.douyin.com/player/video?vid=7531234567890123456&autoplay=0",
    );
  });

  it("does not embed unrecognized or local locators", () => {
    expect(resolveEmbeddedSource("uploaded_media", "asset-id")).toBeNull();
    expect(resolveEmbeddedSource("url", "https://example.com/video/123")).toBeNull();
  });

  it("rebuilds the embedded player at the requested sentence time", () => {
    const bilibili = resolveEmbeddedSource(
      "bilibili",
      "https://www.bilibili.com/video/BV1xx411c7mD",
    );
    const youtube = resolveEmbeddedSource(
      "youtube",
      "https://www.youtube.com/watch?v=abcDEF12345",
    );

    expect(embeddedSourceAtTime(bilibili!, 12_800)).toBe(
      "https://player.bilibili.com/player.html?autoplay=1&bvid=BV1xx411c7mD&t=12",
    );
    expect(embeddedSourceAtTime(youtube!, 65_900)).toBe(
      "https://www.youtube-nocookie.com/embed/abcDEF12345?autoplay=1&rel=0&start=65",
    );
  });

  it("formats the original publication date without using task time", () => {
    expect(formatPublishedAge("2024-01-01", Date.UTC(2025, 0, 1))).toBe(
      "1 年前",
    );
    expect(formatPublishedAge("invalid", Date.UTC(2025, 0, 1))).toBeNull();
  });

  it("uses a structural skeleton while public metadata is loading", () => {
    vi.spyOn(api, "request").mockReturnValue(new Promise(() => undefined));

    render(
      <SourceVideoPanel
        sourceKind="bilibili"
        locator="https://www.bilibili.com/video/BV1xx411c7mD"
        title="公开视频"
      />,
    );

    expect(
      screen.getByRole("status", { name: "正在读取视频信息" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("正在读取视频信息")).not.toBeInTheDocument();
  });
});

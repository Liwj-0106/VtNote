import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TranscriptSegment } from "../../api/types";
import {
  buildOriginalChapters,
  OriginalTextView,
  originalToMarkdown,
  originalToText,
} from "./OriginalTextView";

const segments: TranscriptSegment[] = [
  { id: "seg_000001", start_ms: 0, end_ms: 8_000, text: "开场介绍本期内容。" },
  {
    id: "seg_000002",
    start_ms: 350_000,
    end_ms: 360_000,
    text: "进入第二部分，说明具体经验。",
  },
  {
    id: "seg_000003",
    start_ms: 690_000,
    end_ms: 700_000,
    text: "最后总结重点。",
  },
];

describe("original text chapters", () => {
  it("creates bounded time chapters from existing transcript segments", () => {
    const chapters = buildOriginalChapters(segments);

    expect(chapters).toHaveLength(3);
    expect(chapters.map((chapter) => chapter.startMs)).toEqual([
      0, 350_000, 690_000,
    ]);
    expect(chapters[0].overview).toContain("开场介绍");
  });

  it("exports readable Markdown and text without adding generated content", () => {
    const markdown = originalToMarkdown("测试视频", segments);
    const text = originalToText(segments);

    expect(markdown).toContain("# 测试视频");
    expect(markdown).toContain("## 00:00 开场介绍本期内容");
    expect(text).toContain("进入第二部分，说明具体经验。");
  });

  it("starts with chapter content instead of a repeated video summary block", async () => {
    render(<OriginalTextView segments={segments} />);

    expect(screen.queryByText("视频原文")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /章节/u })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "收起原文" })).toBeInTheDocument();
    expect(screen.getAllByText("开场介绍本期内容。")).toHaveLength(1);

    await userEvent.click(screen.getAllByRole("button", { name: "展开原文" })[0]);
    expect(screen.getAllByText("进入第二部分，说明具体经验。")).toHaveLength(1);
  });

  it("seeks to the selected original sentence", async () => {
    const onSeek = vi.fn();
    render(<OriginalTextView segments={segments} onSeek={onSeek} />);

    await userEvent.click(
      screen.getByRole("button", { name: "开场介绍本期内容。" }),
    );

    expect(onSeek).toHaveBeenCalledWith(0);
  });

  it("expands and scrolls to a requested chapter", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const { rerender } = render(<OriginalTextView segments={segments} />);
    const secondChapter = document.getElementById("original-chapter-2");
    expect(secondChapter).not.toBeNull();
    expect(
      within(secondChapter as HTMLElement).getByRole("button", {
        name: "展开原文",
      }),
    ).toBeInTheDocument();

    rerender(
      <OriginalTextView
        segments={segments}
        chapterRequest={{ id: "original-chapter-2", revision: 1 }}
      />,
    );

    expect(
      within(secondChapter as HTMLElement).getByRole("button", {
        name: "收起原文",
      }),
    ).toBeInTheDocument();
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
    expect(document.activeElement).toBe(secondChapter);
  });
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { TranscriptSegment } from "../api/types";
import { TranscriptViewer } from "./TranscriptViewer";

describe("TranscriptViewer", () => {
  it("bounds the reading DOM and offers an explicit accessible full-text mode", async () => {
    const segments: TranscriptSegment[] = Array.from(
      { length: 600 },
      (_, index) => ({
        id: `seg_${String(index + 1).padStart(6, "0")}`,
        start_ms: index * 1000,
        end_ms: index * 1000 + 900,
        text: `第 ${index + 1} 条字幕`,
      }),
    );
    render(<TranscriptViewer segments={segments} />);
    expect(screen.getAllByTestId("transcript-cue").length).toBeLessThanOrEqual(
      200,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "切换到完整文本模式" }),
    );
    expect(screen.getAllByTestId("transcript-cue")).toHaveLength(600);
  });

  it("searches the complete transcript and jumps between matches", async () => {
    const segments: TranscriptSegment[] = Array.from(
      { length: 220 },
      (_, index) => ({
        id: `seg_${String(index + 1).padStart(6, "0")}`,
        start_ms: index * 1000,
        end_ms: index * 1000 + 900,
        text:
          index === 170 || index === 200
            ? `包含 Agent 的第 ${index + 1} 条字幕`
            : `第 ${index + 1} 条字幕`,
      }),
    );
    render(<TranscriptViewer segments={segments} />);

    const user = userEvent.setup();
    await user.keyboard("{Control>}f{/Control}");
    expect(screen.getByPlaceholderText("搜索字幕")).toHaveFocus();
    await user.type(screen.getByPlaceholderText("搜索字幕"), "Agent");

    expect(await screen.findByText("1/2")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "清除字幕搜索" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Agent").every((node) => node.tagName === "MARK"),
    ).toBe(true);
    expect(
      screen.getAllByText(/220 条字幕/u).find((node) => node.tagName === "SPAN"),
    ).toHaveTextContent("第 2/2 页");

    await user.click(screen.getByRole("button", { name: "下一个匹配项" }));
    expect(await screen.findByText("2/2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "清除字幕搜索" }));
    expect(screen.getByPlaceholderText("搜索字幕")).toHaveValue("");
    expect(screen.getByPlaceholderText("搜索字幕")).toHaveFocus();
  });

  it("turns timestamps into playback controls when seeking is available", async () => {
    const onSeek = vi.fn();
    render(
      <TranscriptViewer
        segments={[
          {
            id: "seg_000001",
            start_ms: 12_000,
            end_ms: 13_000,
            text: "测试字幕",
          },
        ]}
        onSeek={onSeek}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "从 00:12 播放" }));
    expect(onSeek).toHaveBeenCalledWith(12_000);

    await userEvent.click(screen.getByRole("button", { name: "测试字幕" }));
    expect(onSeek).toHaveBeenLastCalledWith(12_000);
    expect(onSeek).toHaveBeenCalledTimes(2);
  });

  it("renders the continuous card reader without the legacy full-text control", () => {
    render(
      <TranscriptViewer
        variant="cards"
        segments={[
          {
            id: "seg_000001",
            start_ms: 0,
            end_ms: 4_000,
            text: "这是一条完整且适合连续阅读的字幕。",
          },
        ]}
      />,
    );

    expect(document.querySelector(".transcript-viewer.is-cards-mode")).not.toBeNull();
    expect(
      screen.queryByRole("button", { name: "切换到完整文本模式" }),
    ).not.toBeInTheDocument();
  });
});

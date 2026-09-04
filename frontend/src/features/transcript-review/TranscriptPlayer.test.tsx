import { createRef } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import {
  TranscriptPlayer,
  type TranscriptPlayerHandle,
} from "./TranscriptPlayer";

const segments = [
  { id: "seg_000001", start_ms: 0, end_ms: 1_000, text: "第一段" },
];

describe("TranscriptPlayer", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses the inline audio route and keeps speed in one compact control", async () => {
    vi.spyOn(api, "request").mockResolvedValue({
      audio: true,
      transcript: true,
      notes: false,
    });
    const onAvailabilityChange = vi.fn();
    const { container } = render(
      <TranscriptPlayer
        itemId="11111111-1111-4111-8111-111111111111"
        segments={segments}
        onActiveSegmentChange={vi.fn()}
        onAvailabilityChange={onAvailabilityChange}
      />,
    );

    expect(await screen.findByRole("group", { name: "音频播放" })).toBeInTheDocument();
    expect(container.querySelector("audio")).toHaveAttribute(
      "src",
      "/api/items/11111111-1111-4111-8111-111111111111/audio?format=m4a&inline=true",
    );
    expect(onAvailabilityChange).toHaveBeenCalledWith(true);

    await userEvent.click(screen.getByRole("button", { name: "播放速度 1 倍" }));
    expect(screen.getByRole("button", { name: "播放速度 1.25 倍" })).toHaveTextContent(
      "1.25×",
    );
  });

  it("stays absent when the task has no audio", async () => {
    vi.spyOn(api, "request").mockResolvedValue({
      audio: false,
      transcript: true,
      notes: false,
    });
    const onAvailabilityChange = vi.fn();
    render(
      <TranscriptPlayer
        itemId="22222222-2222-4222-8222-222222222222"
        segments={segments}
        onActiveSegmentChange={vi.fn()}
        onAvailabilityChange={onAvailabilityChange}
      />,
    );

    await waitFor(() => expect(onAvailabilityChange).toHaveBeenCalledWith(false));
    expect(screen.queryByRole("group", { name: "音频播放" })).not.toBeInTheDocument();
  });

  it("loads deferred audio when a timecode seeks before metadata", async () => {
    vi.spyOn(api, "request").mockResolvedValue({
      audio: true,
      transcript: true,
      notes: false,
    });
    const loadAudio = vi
      .spyOn(HTMLMediaElement.prototype, "load")
      .mockImplementation(() => undefined);
    const player = createRef<TranscriptPlayerHandle>();
    render(
      <TranscriptPlayer
        ref={player}
        itemId="33333333-3333-4333-8333-333333333333"
        segments={segments}
        onActiveSegmentChange={vi.fn()}
      />,
    );

    expect(await screen.findByRole("group", { name: "音频播放" })).toBeInTheDocument();
    act(() => player.current?.seekTo(500));
    expect(loadAudio).toHaveBeenCalledOnce();
  });
});

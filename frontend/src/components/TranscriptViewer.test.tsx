import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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
});

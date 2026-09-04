import { describe, expect, it } from "vitest";
import type { TranscriptSegment } from "../../api/types";
import {
  buildSemanticTranscriptSegments,
  groupTranscriptSegments,
  transcriptToJson,
  transcriptToLrc,
  transcriptToSrt,
  transcriptToTimestampedText,
  transcriptToVtt,
} from "./transcriptGrouping";

const fragments: TranscriptSegment[] = [
  { id: "cue-1", start_ms: 0, end_ms: 600, text: "哈喽，" },
  { id: "cue-2", start_ms: 600, end_ms: 1_300, text: "这里是请匠，" },
  {
    id: "cue-3",
    start_ms: 1_300,
    end_ms: 3_000,
    text: "今天带你们走进租房小能手。",
  },
  {
    id: "cue-4",
    start_ms: 3_000,
    end_ms: 5_500,
    text: "我只花2600块钱租到带屋顶花园的房子。",
  },
];

describe("transcript grouping", () => {
  it("turns raw ASR fragments into readable semantic rows", () => {
    const rows = buildSemanticTranscriptSegments(fragments);

    expect(rows).toHaveLength(2);
    expect(rows[0].text).toBe("哈喽，这里是请匠，今天带你们走进租房小能手。");
    expect(rows[0].sourceIds).toEqual(["cue-1", "cue-2", "cue-3"]);
  });

  it("groups semantic rows from 1 to 20 without losing source ids", () => {
    expect(groupTranscriptSegments(fragments, 1)).toHaveLength(2);
    const grouped = groupTranscriptSegments(fragments, 20);
    expect(grouped).toHaveLength(1);
    expect(grouped[0].sourceIds).toEqual(["cue-1", "cue-2", "cue-3", "cue-4"]);
    expect(grouped[0].text).toContain("房子。");
  });

  it("keeps one-row subtitles concise and exports dedicated subtitle formats", () => {
    const longFlow: TranscriptSegment[] = [
      { id: "long-1", start_ms: 0, end_ms: 3_000, text: "第一段介绍租房背景，" },
      { id: "long-2", start_ms: 3_000, end_ms: 6_000, text: "继续说明预算要求，" },
      { id: "long-3", start_ms: 6_000, end_ms: 9_000, text: "最后给出明确结论。" },
    ];

    expect(buildSemanticTranscriptSegments(longFlow)).toHaveLength(2);
    expect(transcriptToSrt(fragments)).toContain("00:00:00,000 --> 00:00:03,000");
    expect(transcriptToVtt(fragments)).toMatch(/^WEBVTT/u);
    expect(JSON.parse(transcriptToJson(fragments))).toHaveLength(2);
    expect(transcriptToTimestampedText(fragments)).toContain("00:00  哈喽");
    expect(transcriptToLrc(fragments)).toContain("[00:00.00]哈喽");
  });
});

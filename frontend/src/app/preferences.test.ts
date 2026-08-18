import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_PREFERENCES,
  loadPreferences,
  savePreferences,
} from "./preferences";

describe("preferences", () => {
  beforeEach(() => localStorage.clear());

  it("uses current defaults and persists supported choices", () => {
    expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);

    savePreferences({
      defaultExportItems: ["audio", "notes"],
      audioFormat: "mp3",
      subtitleFormat: "txt",
      noteFormat: "txt",
    });

    expect(loadPreferences()).toEqual({
      defaultExportItems: ["audio", "notes"],
      audioFormat: "mp3",
      subtitleFormat: "txt",
      noteFormat: "txt",
    });
  });

  it("normalizes invalid choices, migrates the legacy default, and repairs empty output", () => {
    const cases = [
      {
        stored: {
          defaultExportItems: ["notes", "audio", "notes", "invalid"],
          audioFormat: "m4a",
          subtitleFormat: "srt",
          noteFormat: "markdown",
        },
        expected: ["audio", "notes"],
      },
      { stored: { defaultOutput: "transcript" }, expected: ["transcript"] },
      { stored: { defaultExportItems: [] }, expected: ["transcript"] },
    ];

    for (const item of cases) {
      localStorage.setItem("vtnote.preferences.v1", JSON.stringify(item.stored));
      expect(loadPreferences().defaultExportItems).toEqual(item.expected);
    }
  });
});

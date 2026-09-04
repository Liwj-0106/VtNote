import { describe, expect, it } from "vitest";
import type { DefaultsView, ProfileView } from "../../api/types";
import {
  buildTaskCreationOptions,
  extractSourceUrls,
  isSubtitleFile,
} from "./model";
import {
  availableNotesProfiles,
  initialSummarySettings,
  summaryLanguageOptions,
} from "../summary-settings/model";

const defaults: DefaultsView = {
  asr_mode: "cloud",
  local_asr_engine: "faster_whisper",
  cloud_asr_profile_id: "asr-profile",
  translation_enabled: false,
  translation_profile_id: null,
  translation_target_language: "zh-Hans",
  notes_enabled: true,
  notes_profile_id: "notes-ready",
  notes_template: "summary",
  notes_output_language: "zh-Hant",
  has_custom_prompt: false,
  local_whisper_options: {},
};

function profile(
  id: string,
  overrides: Partial<ProfileView> = {},
): ProfileView {
  return {
    id,
    name: id,
    purpose: "notes",
    connection_id: "connection",
    protocol: "openai_chat_completions",
    base_url: "https://api.example.com/v1",
    model: "model",
    context_length: 16_000,
    options: {},
    revision: 1,
    tested: true,
    test_ok: true,
    test_message: null,
    upload_authorized: false,
    capability_fingerprint: null,
    chat_data_authorized: true,
    ...overrides,
  };
}

describe("task creation model", () => {
  it("keeps source parsing and subtitle detection consistent across entry points", () => {
    expect(
      extractSourceUrls(
        "第一个 https://example.com/a，第二个 https://example.com/b。",
      ),
    ).toEqual(["https://example.com/a", "https://example.com/b"]);
    expect(isSubtitleFile({ name: "captions.VTT" })).toBe(true);
    expect(isSubtitleFile({ name: "recording.mp4" })).toBe(false);
  });

  it("selects only tested and authorized summary profiles", () => {
    const profiles = [
      profile("notes-ready"),
      profile("untested", { tested: false }),
      profile("unauthorized", { chat_data_authorized: false }),
      profile("asr", { purpose: "cloud_asr" }),
    ];

    expect(availableNotesProfiles(profiles).map((item) => item.id)).toEqual([
      "notes-ready",
    ]);
    expect(initialSummarySettings(defaults, profiles, true)).toEqual({
      enabled: true,
      profileId: "notes-ready",
      outputLanguage: "zh-Hant",
    });
  });

  it("builds one task option contract for URL and subtitle uploads", () => {
    const settings = {
      enabled: true,
      profileId: "notes-ready",
      outputLanguage: "en",
    };

    expect(
      buildTaskCreationOptions({
        defaults,
        settings,
        selectedOutputs: ["audio", "transcript", "notes"],
      }),
    ).toMatchObject({
      output_type: "notes",
      audio_export_enabled: true,
      asr_mode: "cloud",
      local_asr_engine: "faster_whisper",
      cloud_asr_profile_id: "asr-profile",
      notes_enabled: true,
      notes_profile_id: "notes-ready",
      notes_output_language: "en",
    });
    expect(
      buildTaskCreationOptions({
        defaults,
        settings: { ...settings, enabled: false },
        selectedOutputs: ["audio"],
        subtitleUpload: true,
      }),
    ).toEqual({
      output_type: "transcript",
      audio_export_enabled: false,
      asr_mode: "cloud",
      local_asr_engine: "faster_whisper",
      translation_enabled: false,
      notes_enabled: false,
      cloud_asr_profile_id: "asr-profile",
    });
  });

  it("keeps an unknown configured language selectable", () => {
    expect(summaryLanguageOptions("fr")[0]).toEqual({
      value: "fr",
      label: "fr",
    });
  });
});

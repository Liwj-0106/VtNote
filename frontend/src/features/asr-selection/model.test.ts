import { describe, expect, it } from "vitest";
import type { DefaultsView } from "../../api/types";
import {
  asrDefaultsPatch,
  asrSelectionFromDefaults,
  localAsrEngineFromSelection,
  localAsrSelection,
  selectedLocalAsrEngine,
} from "./model";

const defaults: DefaultsView = {
  asr_mode: "local",
  local_asr_engine: "faster_whisper",
  cloud_asr_profile_id: null,
  translation_enabled: false,
  translation_profile_id: null,
  translation_target_language: "zh-Hans",
  notes_enabled: false,
  notes_profile_id: null,
  notes_template: "summary",
  notes_output_language: "zh-Hans",
  has_custom_prompt: false,
  local_whisper_options: {},
};

describe("ASR selection model", () => {
  it("maps local engines to stable selection values", () => {
    expect(localAsrSelection("faster_whisper")).toBe(
      "local:faster_whisper",
    );
    expect(localAsrEngineFromSelection("cloud-profile")).toBeNull();
  });

  it("restores the selected local engine from defaults", () => {
    expect(asrSelectionFromDefaults(defaults)).toBe(
      "local:faster_whisper",
    );
    expect(
      asrSelectionFromDefaults({
        ...defaults,
        asr_mode: "auto",
        cloud_asr_profile_id: "cloud-profile",
      }),
    ).toBe("cloud-profile");
  });

  it("builds one defaults patch for local, automatic, and cloud choices", () => {
    expect(asrDefaultsPatch("local:faster_whisper")).toEqual({
      asr_mode: "local",
      local_asr_engine: "faster_whisper",
      cloud_asr_profile_id: null,
    });
    expect(asrDefaultsPatch("auto")).toEqual({
      asr_mode: "auto",
      cloud_asr_profile_id: null,
    });
    expect(asrDefaultsPatch("cloud-profile")).toEqual({
      asr_mode: "auto",
      cloud_asr_profile_id: "cloud-profile",
    });
  });

  it("keeps the configured local engine while cloud ASR is selected", () => {
    expect(selectedLocalAsrEngine("cloud-profile", defaults)).toBe(
      "faster_whisper",
    );
    expect(selectedLocalAsrEngine("auto", null)).toBe("faster_whisper");
  });
});

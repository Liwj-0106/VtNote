import type { DefaultsView, LocalAsrEngine } from "../../api/types";

const LOCAL_SELECTION_PREFIX = "local:";

export type AsrDefaultsPatch =
  | {
      asr_mode: "local";
      local_asr_engine: LocalAsrEngine;
      cloud_asr_profile_id: null;
    }
  | {
      asr_mode: "auto";
      cloud_asr_profile_id: string | null;
    };

export function localAsrSelection(engine: LocalAsrEngine): string {
  return `${LOCAL_SELECTION_PREFIX}${engine}`;
}

export function localAsrEngineFromSelection(
  selection: string,
): LocalAsrEngine | null {
  if (selection === localAsrSelection("faster_whisper")) {
    return "faster_whisper";
  }
  if (selection === localAsrSelection("sensevoice_sherpa_onnx")) {
    return "sensevoice_sherpa_onnx";
  }
  return null;
}

export function asrSelectionFromDefaults(defaults: DefaultsView): string {
  if (defaults.asr_mode === "local") {
    return localAsrSelection(defaults.local_asr_engine);
  }
  return defaults.cloud_asr_profile_id ?? "auto";
}

export function asrDefaultsPatch(selection: string): AsrDefaultsPatch {
  const localEngine = localAsrEngineFromSelection(selection);
  if (localEngine) {
    return {
      asr_mode: "local",
      local_asr_engine: localEngine,
      cloud_asr_profile_id: null,
    };
  }
  return {
    asr_mode: "auto",
    cloud_asr_profile_id: selection === "auto" ? null : selection,
  };
}

export function selectedLocalAsrEngine(
  selection: string,
  defaults: DefaultsView | null,
): LocalAsrEngine {
  return (
    localAsrEngineFromSelection(selection) ??
    defaults?.local_asr_engine ??
    "faster_whisper"
  );
}

import type { DefaultsView } from "../../api/types";
import type { ExportItem } from "../../app/preferences";
import type { SummaryTaskSettings } from "../summary-settings/model";

const SOURCE_URL_PATTERN = /https:\/\/[^\s<>"'，。；：！？）》】」』…]+/giu;
const SUBTITLE_EXTENSIONS = new Set(["srt", "vtt", "ass", "txt"]);

export interface TaskCreationOptions {
  output_type: "audio" | "transcript" | "notes";
  audio_export_enabled: boolean;
  asr_mode: DefaultsView["asr_mode"];
  local_asr_engine: DefaultsView["local_asr_engine"];
  translation_enabled: false;
  notes_enabled: boolean;
  cloud_asr_profile_id?: string;
  notes_profile_id?: string;
  notes_output_language?: string;
}

export function extractSourceUrls(value: string): string[] {
  return value.match(SOURCE_URL_PATTERN) ?? [];
}

export function isSubtitleFile(file: Pick<File, "name">): boolean {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return SUBTITLE_EXTENSIONS.has(extension);
}

export function buildTaskCreationOptions({
  defaults,
  settings,
  selectedOutputs,
  subtitleUpload = false,
}: {
  defaults: DefaultsView | null;
  settings: SummaryTaskSettings;
  selectedOutputs: readonly ExportItem[];
  subtitleUpload?: boolean;
}): TaskCreationOptions {
  const notesRequested = settings.enabled && Boolean(settings.profileId);
  const transcriptRequested =
    selectedOutputs.includes("transcript") || selectedOutputs.includes("notes");
  const outputType = subtitleUpload
    ? notesRequested
      ? "notes"
      : "transcript"
    : notesRequested
      ? "notes"
      : transcriptRequested
        ? "transcript"
        : "audio";

  return {
    output_type: outputType,
    audio_export_enabled:
      !subtitleUpload && selectedOutputs.includes("audio"),
    asr_mode: defaults?.asr_mode ?? "auto",
    local_asr_engine: defaults?.local_asr_engine ?? "faster_whisper",
    translation_enabled: false,
    notes_enabled: notesRequested,
    ...(defaults?.cloud_asr_profile_id
      ? { cloud_asr_profile_id: defaults.cloud_asr_profile_id }
      : {}),
    ...(notesRequested
      ? {
          notes_profile_id: settings.profileId,
          notes_output_language: settings.outputLanguage,
        }
      : {}),
  };
}

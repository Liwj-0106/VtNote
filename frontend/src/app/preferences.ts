export type ExportItem = "audio" | "transcript" | "notes";
export type AudioFormat = "m4a" | "mp3";
export type SubtitleFormat = "srt" | "txt";
export type NoteFormat = "markdown" | "txt";

export interface AppPreferences {
  defaultExportItems: ExportItem[];
  audioFormat: AudioFormat;
  subtitleFormat: SubtitleFormat;
  noteFormat: NoteFormat;
}

const PREFERENCES_KEY = "vtnote.preferences.v1";

export const DEFAULT_PREFERENCES: AppPreferences = {
  defaultExportItems: ["audio", "transcript", "notes"],
  audioFormat: "m4a",
  subtitleFormat: "srt",
  noteFormat: "markdown",
};

export function loadPreferences(): AppPreferences {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFERENCES_KEY) ?? "null") as
      | (Partial<AppPreferences> & { defaultOutput?: string })
      | null;
    const supportedExportItems: ExportItem[] = ["audio", "transcript", "notes"];
    const selectedExportItems = Array.isArray(raw?.defaultExportItems)
      ? supportedExportItems.filter((item) => raw.defaultExportItems?.includes(item))
      : supportedExportItems.includes(raw?.defaultOutput as ExportItem)
        ? [raw!.defaultOutput as ExportItem]
        : DEFAULT_PREFERENCES.defaultExportItems;
    const defaultExportItems =
      selectedExportItems.length > 0 ? selectedExportItems : (["transcript"] as ExportItem[]);
    return {
      defaultExportItems,
      audioFormat: ["m4a", "mp3"].includes(raw?.audioFormat ?? "")
        ? (raw!.audioFormat as AudioFormat)
        : DEFAULT_PREFERENCES.audioFormat,
      subtitleFormat: ["srt", "txt"].includes(raw?.subtitleFormat ?? "")
        ? (raw!.subtitleFormat as SubtitleFormat)
        : DEFAULT_PREFERENCES.subtitleFormat,
      noteFormat: ["markdown", "txt"].includes(raw?.noteFormat ?? "")
        ? (raw!.noteFormat as NoteFormat)
        : DEFAULT_PREFERENCES.noteFormat,
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export function savePreferences(preferences: AppPreferences): void {
  localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
}

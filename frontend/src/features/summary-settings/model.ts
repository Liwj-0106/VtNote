import type { DefaultsView, ProfileView } from "../../api/types";
import { availableProfiles } from "../profile-selection/model";

export interface SummaryTaskSettings {
  enabled: boolean;
  profileId: string;
  outputLanguage: string;
}

export const SUMMARY_LANGUAGE_OPTIONS = [
  { value: "zh-Hans", label: "简体中文" },
  { value: "zh-Hant", label: "繁體中文" },
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
] as const;

export function availableNotesProfiles(profiles: ProfileView[]): ProfileView[] {
  return availableProfiles(profiles, "notes");
}

export function initialSummarySettings(
  defaults: DefaultsView,
  profiles: ProfileView[],
  enabled: boolean,
): SummaryTaskSettings {
  const available = availableNotesProfiles(profiles);
  return {
    enabled,
    profileId:
      available.find((profile) => profile.id === defaults.notes_profile_id)?.id ??
      available[0]?.id ??
      "",
    outputLanguage: defaults.notes_output_language,
  };
}

export function summaryLanguageOptions(selectedLanguage: string) {
  return SUMMARY_LANGUAGE_OPTIONS.some(
    (option) => option.value === selectedLanguage,
  )
    ? [...SUMMARY_LANGUAGE_OPTIONS]
    : [
        { value: selectedLanguage, label: selectedLanguage },
        ...SUMMARY_LANGUAGE_OPTIONS,
      ];
}

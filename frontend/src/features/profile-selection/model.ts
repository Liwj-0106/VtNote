import type { ProfileView } from "../../api/types";

export function availableProfiles(
  profiles: ProfileView[],
  purpose: ProfileView["purpose"],
): ProfileView[] {
  return profiles.filter(
    (profile) =>
      profile.purpose === purpose &&
      profile.tested &&
      profile.test_ok === true &&
      (purpose === "cloud_asr"
        ? profile.upload_authorized
        : profile.chat_data_authorized),
  );
}

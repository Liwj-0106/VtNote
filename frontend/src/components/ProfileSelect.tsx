import type { ProfileView } from "../api/types";

export function ProfileSelect({
  id,
  label,
  purpose,
  profiles,
  value,
  onChange,
  required,
}: {
  id: string;
  label: string;
  purpose: ProfileView["purpose"];
  profiles: ProfileView[];
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  const available = profiles.filter(
    (profile) =>
      profile.purpose === purpose &&
      profile.tested &&
      profile.test_ok === true &&
      (purpose === "cloud_asr"
        ? profile.upload_authorized
        : profile.chat_data_authorized),
  );
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="select-input"
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">选择已测试并授权的配置</option>
        {available.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.name} · {profile.model}
          </option>
        ))}
      </select>
      {available.length === 0 && (
        <p className="field-hint">还没有可用配置，请先前往设置。</p>
      )}
    </div>
  );
}

import type { ProfileView } from "../api/types";
import { availableProfiles } from "../features/profile-selection/model";
import { SelectMenu } from "./SelectMenu";

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
  const available = availableProfiles(profiles, purpose);
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <SelectMenu
        id={id}
        ariaLabel={label}
        value={value}
        required={required}
        onChange={onChange}
        options={[
          { value: "", label: "选择已测试并授权的配置" },
          ...available.map((profile) => ({
            value: profile.id,
            label: `${profile.name} · ${profile.model}`,
          })),
        ]}
      />
      {available.length === 0 && (
        <p className="field-hint">还没有可用配置，请先前往设置。</p>
      )}
    </div>
  );
}

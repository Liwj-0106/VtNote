import { useEffect, useState } from "react";
import {
  DEFAULT_THEME_COLORS,
  useInterfacePreferences,
  type InterfaceLanguage,
  type ThemePreference,
} from "../app/interfacePreferences";
import { MoonIcon, SunIcon, SystemThemeIcon } from "../app/icons";

const themes: Array<{
  value: ThemePreference;
  label: "theme.light" | "theme.dark" | "theme.system";
  icon: typeof SunIcon;
}> = [
  {
    value: "light",
    label: "theme.light",
    icon: SunIcon,
  },
  {
    value: "dark",
    label: "theme.dark",
    icon: MoonIcon,
  },
  {
    value: "system",
    label: "theme.system",
    icon: SystemThemeIcon,
  },
];

const languages: Array<{
  value: InterfaceLanguage;
  label: "language.chinese" | "language.english";
}> = [
  {
    value: "zh-CN",
    label: "language.chinese",
  },
  {
    value: "en",
    label: "language.english",
  },
];

function normalizeColorDraft(value: string): string | null {
  const trimmed = value.trim();
  const normalized = trimmed.startsWith("#") ? trimmed : `#${trimmed}`;
  return /^#[0-9a-f]{6}$/i.test(normalized) ? normalized.toLowerCase() : null;
}

function ColorControl({
  id,
  label,
  pickerLabel,
  value,
  valueLabel,
  onChange,
}: {
  id: string;
  label: string;
  pickerLabel: string;
  value: string;
  valueLabel: string;
  onChange: (value: string) => void;
}) {
  const [draft, setDraft] = useState(value.toUpperCase());

  useEffect(() => setDraft(value.toUpperCase()), [value]);

  const commitDraft = () => {
    const normalized = normalizeColorDraft(draft);
    if (normalized) {
      setDraft(normalized.toUpperCase());
      onChange(normalized);
    } else {
      setDraft(value.toUpperCase());
    }
  };

  return (
    <div className="color-setting-row">
      <label htmlFor={`${id}-value`}>{label}</label>
      <div className="color-value-control">
        <input
          id={`${id}-picker`}
          type="color"
          aria-label={`${label} ${pickerLabel}`}
          title={`${label} ${pickerLabel}`}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <input
          id={`${id}-value`}
          className="color-hex-input"
          type="text"
          aria-label={`${label} ${valueLabel}`}
          value={draft}
          maxLength={7}
          spellCheck={false}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitDraft}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
            if (event.key === "Escape") {
              setDraft(value.toUpperCase());
              event.currentTarget.blur();
            }
          }}
        />
      </div>
    </div>
  );
}

export function GeneralSettingsPage() {
  const {
    accentColor,
    backgroundColor,
    foregroundColor,
    language,
    resetColors,
    setAccentColor,
    setBackgroundColor,
    setForegroundColor,
    setLanguage,
    setTheme,
    text,
    theme,
  } =
    useInterfacePreferences();
  const resolvedTheme =
    document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const defaultColors = DEFAULT_THEME_COLORS[resolvedTheme];
  const displayedAccentColor = accentColor ?? defaultColors.accent;
  const displayedBackgroundColor = backgroundColor ?? defaultColors.background;
  const displayedForegroundColor = foregroundColor ?? defaultColors.foreground;
  const hasCustomColors = Boolean(
    accentColor || backgroundColor || foregroundColor,
  );

  return (
    <div className="settings-page general-settings-page">
      <header className="settings-page-header">
        <div>
          <h2>{text("settings.general")}</h2>
        </div>
      </header>

      <section className="preference-section settings-card" aria-labelledby="appearance-settings">
        <div className="settings-section-heading">
          <h3 id="appearance-settings">{text("general.appearance")}</h3>
        </div>
        <fieldset className="settings-choice-fieldset">
          <legend>
            <span>{text("general.theme")}</span>
          </legend>
          <div className="theme-choice-grid">
            {themes.map((option) => {
              const Icon = option.icon;
              return (
                <label
                  className="theme-choice"
                  data-selected={theme === option.value ? "true" : "false"}
                  key={option.value}
                >
                  <input
                    type="radio"
                    name="theme"
                    value={option.value}
                    checked={theme === option.value}
                    onChange={() => setTheme(option.value)}
                  />
                  <span
                    className="theme-preview"
                    data-preview-theme={option.value}
                    aria-hidden="true"
                  >
                    <span className="theme-preview-sidebar" />
                    <span className="theme-preview-content">
                      <span />
                      <span />
                      <span />
                    </span>
                  </span>
                  <span className="theme-choice-copy">
                    <span className="theme-choice-title">
                      <Icon />
                      <strong>{text(option.label)}</strong>
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="color-settings">
            <div className="color-settings-heading">
              <h4>{text("general.colors")}</h4>
              {hasCustomColors ? (
                <button
                  type="button"
                  className="button button-quiet color-settings-reset"
                  onClick={resetColors}
                >
                  {text("general.resetColors")}
                </button>
              ) : null}
            </div>
            <ColorControl
              id="interface-accent-color"
              label={text("general.accentColor")}
              pickerLabel={text("general.colorPicker")}
              value={displayedAccentColor}
              valueLabel={text("general.colorValue")}
              onChange={setAccentColor}
            />
            <ColorControl
              id="interface-background-color"
              label={text("general.backgroundColor")}
              pickerLabel={text("general.colorPicker")}
              value={displayedBackgroundColor}
              valueLabel={text("general.colorValue")}
              onChange={setBackgroundColor}
            />
            <ColorControl
              id="interface-foreground-color"
              label={text("general.foregroundColor")}
              pickerLabel={text("general.colorPicker")}
              value={displayedForegroundColor}
              valueLabel={text("general.colorValue")}
              onChange={setForegroundColor}
            />
          </div>
        </fieldset>
      </section>

      <section className="preference-section settings-card" aria-labelledby="language-settings">
        <div className="settings-section-heading">
          <h3 id="language-settings">{text("general.language")}</h3>
        </div>
        <fieldset className="settings-choice-fieldset language-choice-fieldset">
          <legend className="visually-hidden">{text("general.language")}</legend>
          <div className="language-choice-grid">
            {languages.map((option) => (
              <label
                className="language-choice"
                data-selected={language === option.value ? "true" : "false"}
                key={option.value}
              >
                <input
                  type="radio"
                  name="language"
                  value={option.value}
                  checked={language === option.value}
                  onChange={() => setLanguage(option.value)}
                />
                <span className="language-choice-copy">
                  <strong>{text(option.label)}</strong>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      </section>
    </div>
  );
}

import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { ExportSettings } from "../api/types";
import { useInterfacePreferences } from "../app/interfacePreferences";
import {
  loadPreferences,
  savePreferences,
  type AppPreferences,
  type ExportItem,
} from "../app/preferences";
import { SelectMenu } from "../components/SelectMenu";
import { MotionPresence } from "../components/MotionPresence";
import { Skeleton, SkeletonStatus } from "../components/Skeleton";

const exportItems: ExportItem[] = ["audio", "transcript", "notes"];

export function SettingsPage() {
  const { text } = useInterfacePreferences();
  const [preferences, setPreferences] = useState<AppPreferences>(loadPreferences);
  const [exportSettings, setExportSettings] = useState<ExportSettings | null>(null);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [selectingDirectory, setSelectingDirectory] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api
      .request<ExportSettings>("/api/export-settings", { signal: controller.signal })
      .then(setExportSettings)
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setDirectoryError(
            caught instanceof ApiError ? caught.message : text("export.readError"),
          );
        }
      });
    return () => controller.abort();
  }, [text]);

  const exportItemLabel = (item: ExportItem) =>
    text(
      item === "audio"
        ? "export.audio"
        : item === "transcript"
          ? "export.transcript"
          : "export.notes",
    );

  const updatePreference = <Key extends keyof AppPreferences>(
    key: Key,
    value: AppPreferences[Key],
  ) => {
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    savePreferences(next);
  };

  const toggleExportItem = (item: ExportItem) => {
    const selected = preferences.defaultExportItems.includes(item);
    if (selected && preferences.defaultExportItems.length === 1) return;
    updatePreference(
      "defaultExportItems",
      selected
        ? preferences.defaultExportItems.filter((value) => value !== item)
        : exportItems.filter(
            (value) =>
              value === item || preferences.defaultExportItems.includes(value),
          ),
    );
  };

  const chooseDirectory = async () => {
    setSelectingDirectory(true);
    setDirectoryError(null);
    try {
      const selected = await api.request<{ canceled: boolean; directory: string | null }>(
        "/api/system/pick-directory",
        { method: "POST" },
      );
      if (!selected.canceled && selected.directory) {
        setExportSettings(
          await api.request<ExportSettings>("/api/export-settings", {
            method: "PATCH",
            body: { directory: selected.directory },
          }),
        );
      }
    } catch (caught) {
      setDirectoryError(
        caught instanceof ApiError ? caught.message : text("export.chooseError"),
      );
    } finally {
      setSelectingDirectory(false);
    }
  };

  const restoreDefaultDirectory = async () => {
    setDirectoryError(null);
    try {
      setExportSettings(
        await api.request<ExportSettings>("/api/export-settings", {
          method: "PATCH",
          body: { use_default: true },
        }),
      );
    } catch (caught) {
      setDirectoryError(
        caught instanceof ApiError ? caught.message : text("export.restoreError"),
      );
    }
  };

  return (
    <div className="settings-page">
      <header className="settings-page-header">
        <div>
          <h2>{text("settings.export")}</h2>
        </div>
      </header>

      <section
        className="preference-section settings-card"
        aria-labelledby="default-export"
      >
        <h3 id="default-export">{text("export.default")}</h3>
        <div className="preference-list">
          {exportItems.map((item) => (
            <label className="preference-row" key={item}>
              <span className="preference-name">{exportItemLabel(item)}</span>
              <span className="settings-switch">
                <input
                  type="checkbox"
                  checked={preferences.defaultExportItems.includes(item)}
                  disabled={
                    preferences.defaultExportItems.length === 1 &&
                    preferences.defaultExportItems.includes(item)
                  }
                  onChange={() => toggleExportItem(item)}
                />
                <span aria-hidden="true" />
              </span>
            </label>
          ))}
        </div>
      </section>

      <section
        className="preference-section settings-card"
        aria-labelledby="export-format"
      >
        <h3 id="export-format">{text("export.format")}</h3>
        <div className="preference-list">
          <div className="preference-row">
            <span className="preference-name">{text("export.audio")}</span>
            <SelectMenu
              className="preference-menu"
              ariaLabel={text("export.audioFormat")}
              value={preferences.audioFormat}
              onChange={(value) =>
                updatePreference(
                  "audioFormat",
                  value as AppPreferences["audioFormat"],
                )
              }
              options={[
                { value: "m4a", label: "M4A" },
                { value: "mp3", label: "MP3" },
              ]}
            />
          </div>
          <div className="preference-row">
            <span className="preference-name">{text("export.transcript")}</span>
            <SelectMenu
              className="preference-menu"
              ariaLabel={text("export.transcriptFormat")}
              value={preferences.subtitleFormat}
              onChange={(value) =>
                updatePreference(
                  "subtitleFormat",
                  value as AppPreferences["subtitleFormat"],
                )
              }
              options={[
                { value: "srt", label: "SRT" },
                { value: "txt", label: "TXT" },
              ]}
            />
          </div>
          <div className="preference-row">
            <span className="preference-name">{text("export.notes")}</span>
            <SelectMenu
              className="preference-menu"
              ariaLabel={text("export.notesFormat")}
              value={preferences.noteFormat}
              onChange={(value) =>
                updatePreference(
                  "noteFormat",
                  value as AppPreferences["noteFormat"],
                )
              }
              options={[
                { value: "markdown", label: "Markdown" },
                { value: "txt", label: "TXT" },
              ]}
            />
          </div>
        </div>
      </section>

      <section
        className="preference-section settings-card"
        aria-labelledby="export-directory"
      >
        <h3 id="export-directory">{text("export.directory")}</h3>
        <div className="export-directory-row">
          {exportSettings ? (
            <output title={exportSettings.directory}>
              {exportSettings.directory}
            </output>
          ) : directoryError ? (
            <span aria-hidden="true" />
          ) : (
            <SkeletonStatus label={text("export.reading")}>
              <Skeleton style={{ width: "min(72%, 28rem)" }} />
            </SkeletonStatus>
          )}
          <div className="export-directory-actions">
            {!exportSettings?.is_default && (
              <button
                type="button"
                className="button button-quiet"
                onClick={() => void restoreDefaultDirectory()}
              >
                {text("export.restoreDefault")}
              </button>
            )}
            <button
              type="button"
              className="button"
              disabled={selectingDirectory}
              onClick={() => void chooseDirectory()}
            >
              {selectingDirectory
                ? text("export.choosing")
                : text("export.choose")}
            </button>
          </div>
        </div>
        <MotionPresence present={Boolean(directoryError)}>
          {directoryError ? <p className="field-error">{directoryError}</p> : null}
        </MotionPresence>
      </section>
    </div>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  ConnectionView,
  DefaultsView,
  NotesPromptView,
  ProfileView,
} from "../api/types";
import { useInterfacePreferences } from "../app/interfacePreferences";
import { InlineNotice } from "../components/InlineNotice";
import { MotionPresence } from "../components/MotionPresence";
import { SelectMenu } from "../components/SelectMenu";
import { SettingsRowsSkeleton } from "../components/Skeleton";
import {
  InlineAsrConnections,
  InlineSummaryConnections,
} from "../features/model-settings/InlineModelConnections";
import {
  asrDefaultsPatch,
  localAsrEngineFromSelection,
  asrSelectionFromDefaults,
  localAsrSelection,
} from "../features/asr-selection/model";
import { SenseVoiceAssetControl } from "../features/asr-selection/SenseVoiceAssetControl";
import { availableProfiles } from "../features/profile-selection/model";

function profileLabel(profile: ProfileView): string {
  if (profile.model === "glm-5.1") return "GLM-5.1";
  if (profile.model === "deepseek-v4-flash") return "DeepSeek V4 Flash";
  return profile.name;
}

export function ModelSettingsPage() {
  const { text } = useInterfacePreferences();
  const [defaults, setDefaults] = useState<DefaultsView | null>(null);
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [connections, setConnections] = useState<ConnectionView[]>([]);
  const [asrSelection, setAsrSelection] = useState("auto");
  const [savingAsrSelection, setSavingAsrSelection] = useState(false);
  const [notesPrompt, setNotesPrompt] = useState("");
  const [savedNotesPrompt, setSavedNotesPrompt] = useState("");
  const [notesPromptIsCustom, setNotesPromptIsCustom] = useState(false);
  const [savingNotesPrompt, setSavingNotesPrompt] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const asrSaveInFlight = useRef(false);

  const loadSettings = useCallback(async (signal?: AbortSignal) => {
    const [nextDefaults, nextProfiles, nextConnections, nextPrompt] =
      await Promise.all([
        api.request<DefaultsView>("/api/defaults", { signal }),
        api.request<ProfileView[]>("/api/profiles", { signal }),
        api.request<ConnectionView[]>("/api/connections", { signal }),
        api.request<NotesPromptView>("/api/defaults/notes-prompt/reveal", {
          method: "POST",
          signal,
        }),
      ]);
    if (signal?.aborted) return;
    setDefaults(nextDefaults);
    setProfiles(nextProfiles);
    setConnections(nextConnections);
    setAsrSelection(asrSelectionFromDefaults(nextDefaults));
    setNotesPrompt(nextPrompt.prompt);
    setSavedNotesPrompt(nextPrompt.prompt);
    setNotesPromptIsCustom(nextPrompt.is_custom);
    setLoadError(false);
    setSaveError(false);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadSettings(controller.signal)
      .catch(() => {
        if (!controller.signal.aborted) setLoadError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setInitialLoading(false);
      });
    return () => controller.abort();
  }, [loadSettings]);

  const refreshSettings = useCallback(
    () => loadSettings(),
    [loadSettings],
  );

  const patchDefaults = async (changes: Record<string, unknown>) => {
    try {
      const next = await api.request<DefaultsView>("/api/defaults", {
        method: "PATCH",
        body: changes,
      });
      setDefaults(next);
      setSaveError(false);
      return next;
    } catch {
      setSaveError(true);
      return null;
    }
  };

  const saveAsrSelection = async (value: string) => {
    if (!defaults || asrSaveInFlight.current) return;
    const previousSelection = asrSelectionFromDefaults(defaults);
    asrSaveInFlight.current = true;
    setSavingAsrSelection(true);
    setSaveError(false);
    setAsrSelection(value);
    try {
      const next = await api.request<DefaultsView>("/api/defaults", {
        method: "PATCH",
        body: asrDefaultsPatch(value),
      });
      setDefaults(next);
      setAsrSelection(asrSelectionFromDefaults(next));
    } catch {
      setAsrSelection(previousSelection);
      setSaveError(true);
    } finally {
      asrSaveInFlight.current = false;
      setSavingAsrSelection(false);
    }
  };

  const saveNotesPrompt = async () => {
    const normalizedPrompt = notesPrompt.trim();
    if (!normalizedPrompt) {
      setNotesPrompt(savedNotesPrompt);
      return;
    }
    if (normalizedPrompt === savedNotesPrompt) return;
    setSavingNotesPrompt(true);
    const next = await patchDefaults({
      notes_template: "custom",
      notes_custom_prompt: normalizedPrompt,
    });
    if (next) {
      setNotesPrompt(normalizedPrompt);
      setSavedNotesPrompt(normalizedPrompt);
      setNotesPromptIsCustom(true);
    }
    setSavingNotesPrompt(false);
  };

  const resetNotesPrompt = async () => {
    setSavingNotesPrompt(true);
    const next = await patchDefaults({
      notes_template: "summary",
      notes_custom_prompt: null,
    });
    if (next) {
      try {
        const revealed = await api.request<NotesPromptView>(
          "/api/defaults/notes-prompt/reveal",
          { method: "POST" },
        );
        setNotesPrompt(revealed.prompt);
        setSavedNotesPrompt(revealed.prompt);
        setNotesPromptIsCustom(revealed.is_custom);
        setSaveError(false);
      } catch {
        setSaveError(true);
      }
    }
    setSavingNotesPrompt(false);
  };

  const asrProfiles = availableProfiles(profiles, "cloud_asr");
  const asrOptions = connections
    .filter((connection) => connection.protocol === "tencent_recording_asr")
    .map((connection) => {
      const profile = asrProfiles.find(
        (candidate) => candidate.connection_id === connection.id,
      );
      return {
        connection,
        profile,
        value: profile?.id ?? `connection:${connection.id}`,
      };
    });
  const noteProfiles = availableProfiles(profiles, "notes");
  const activeLocalEngine =
    localAsrEngineFromSelection(asrSelection) ??
    defaults?.local_asr_engine ??
    "faster_whisper";
  return (
    <div className="settings-page">
      <header className="settings-page-header">
        <div>
          <h2>{text("settings.models")}</h2>
        </div>
      </header>

      <MotionPresence present={loadError || saveError}>
        {loadError || saveError ? (
          <InlineNotice tone="danger">
            {loadError ? text("models.loadError") : text("models.saveError")}
          </InlineNotice>
        ) : null}
      </MotionPresence>

      {initialLoading ? (
        <>
          <section
            className="preference-section settings-card"
            aria-labelledby="speech-models"
          >
            <h3 id="speech-models">{text("models.speech")}</h3>
            <SettingsRowsSkeleton
              label={`${text("models.speech")} ${text("export.reading")}`}
              count={4}
            />
          </section>
          <section
            className="preference-section settings-card"
            aria-labelledby="summary-models"
          >
            <h3 id="summary-models">{text("models.summary")}</h3>
            <SettingsRowsSkeleton
              label={`${text("models.summary")} ${text("export.reading")}`}
              count={3}
            />
          </section>
        </>
      ) : (
        <>
          <section
            className="preference-section settings-card"
            aria-labelledby="speech-models"
          >
            <h3 id="speech-models">{text("models.speech")}</h3>
            <div className="preference-list">
              <div className="preference-row">
                <span className="preference-name">{text("models.default")}</span>
                <SelectMenu
                  className="preference-menu preference-menu-wide"
                  ariaLabel={text("models.defaultSpeech")}
                  value={asrSelection}
                  disabled={savingAsrSelection}
                  onChange={(value) => {
                    if (value.startsWith("connection:")) return;
                    void saveAsrSelection(value);
                  }}
                  options={[
                    { value: "auto", label: text("models.auto") },
                    {
                      value: localAsrSelection("faster_whisper"),
                      label: text("models.fasterWhisper"),
                    },
                    {
                      value: localAsrSelection("sensevoice_sherpa_onnx"),
                      label: text("models.senseVoice"),
                    },
                    ...asrOptions.map(({ connection, profile, value }) => ({
                      value,
                      label: connection.name,
                      disabled: !profile,
                    })),
                  ]}
                />
              </div>
              {activeLocalEngine === "faster_whisper" ? <div className="preference-row">
                <span className="preference-name">
                  {text("models.cpuFallback")}
                </span>
                <label className="settings-switch">
                  <input
                    type="checkbox"
                    aria-label={text("models.cpuFallback")}
                    checked={
                      defaults?.local_whisper_options
                        .cpu_fallback_enabled === true
                    }
                    onChange={(event) =>
                      void patchDefaults({
                        local_whisper_options: {
                          cpu_fallback_enabled: event.target.checked,
                        },
                      })
                    }
                  />
                  <span aria-hidden="true" />
                </label>
              </div> : null}
              {activeLocalEngine === "faster_whisper" ? <div className="preference-row">
                <span className="preference-name">
                  {text("models.speakerDiarization")}
                </span>
                <label className="settings-switch">
                  <input
                    type="checkbox"
                    aria-label={text("models.speakerDiarization")}
                    checked={
                      defaults?.local_whisper_options
                        .speaker_diarization_enabled === true
                    }
                    onChange={(event) =>
                      void patchDefaults({
                        local_whisper_options: {
                          speaker_diarization_enabled: event.target.checked,
                        },
                      })
                    }
                  />
                  <span aria-hidden="true" />
                </label>
              </div> : null}
              {activeLocalEngine === "sensevoice_sherpa_onnx" ? (
                <SenseVoiceAssetControl />
              ) : null}
              <InlineAsrConnections
                connections={connections}
                onChanged={refreshSettings}
              />
            </div>
          </section>

          <section
            className="preference-section settings-card"
            aria-labelledby="summary-models"
          >
            <h3 id="summary-models">{text("models.summary")}</h3>
            <div className="preference-list">
              <div className="preference-row">
                <span className="preference-name">{text("models.default")}</span>
                <SelectMenu
                  className="preference-menu preference-menu-wide"
                  ariaLabel={text("models.defaultSummary")}
                  value={defaults?.notes_profile_id ?? ""}
                  onChange={(value) =>
                    void patchDefaults({
                      notes_enabled: Boolean(value),
                      notes_profile_id: value || null,
                    })
                  }
                  options={[
                    { value: "", label: text("models.notConfigured") },
                    ...noteProfiles.map((profile) => ({
                      value: profile.id,
                      label: profileLabel(profile),
                    })),
                  ]}
                />
              </div>
              <InlineSummaryConnections
                connections={connections}
                onChanged={refreshSettings}
              />
              <div className="preference-prompt-editor">
                <div className="preference-prompt-heading">
                  <label
                    className="preference-name"
                    htmlFor="notes-prompt-template"
                  >
                    {text("models.promptTemplate")}
                  </label>
                  {notesPromptIsCustom && (
                    <button
                      type="button"
                      className="button button-quiet prompt-reset-button"
                      disabled={savingNotesPrompt}
                      onClick={() => void resetNotesPrompt()}
                    >
                      {text("models.restoreDefault")}
                    </button>
                  )}
                </div>
                <textarea
                  id="notes-prompt-template"
                  className="prompt-template-input"
                  value={notesPrompt}
                  maxLength={8000}
                  aria-busy={savingNotesPrompt}
                  disabled={savingNotesPrompt}
                  onChange={(event) => setNotesPrompt(event.target.value)}
                  onBlur={() => void saveNotesPrompt()}
                />
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { ProfileView } from "../../api/types";
import { FormDialog } from "../../components/FormDialog";
import { InlineNotice } from "../../components/InlineNotice";
import { SelectMenu } from "../../components/SelectMenu";
import { Skeleton } from "../../components/Skeleton";
import {
  availableNotesProfiles,
  summaryLanguageOptions,
} from "../summary-settings/model";

export interface SummarySettings {
  profileId: string;
  profileRevision: number;
  modelLabel: string;
  outputLanguage: string;
}

export function SummarySettingsDialog({
  open,
  initialProfileId,
  initialOutputLanguage,
  onClose,
  onSave,
}: {
  open: boolean;
  initialProfileId: string;
  initialOutputLanguage: string;
  onClose: () => void;
  onSave: (settings: SummarySettings) => void;
}) {
  const [profiles, setProfiles] = useState<ProfileView[]>([]);
  const [profileId, setProfileId] = useState(initialProfileId);
  const [outputLanguage, setOutputLanguage] = useState(initialOutputLanguage);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const available = useMemo(() => availableNotesProfiles(profiles), [profiles]);
  const languageOptions = useMemo(
    () => summaryLanguageOptions(outputLanguage),
    [outputLanguage],
  );
  const selectedProfile = available.find((profile) => profile.id === profileId);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setProfileId(initialProfileId);
    setOutputLanguage(initialOutputLanguage);
    setError(null);
    setLoading(true);
    api
      .request<ProfileView[]>("/api/profiles", { signal: controller.signal })
      .then((nextProfiles) => {
        if (controller.signal.aborted) return;
        const nextAvailable = availableNotesProfiles(nextProfiles);
        setProfiles(nextProfiles);
        setProfileId((current) =>
          nextAvailable.some((profile) => profile.id === current)
            ? current
            : (nextAvailable[0]?.id ?? ""),
        );
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(caught instanceof ApiError ? caught.message : "模型读取失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [initialOutputLanguage, initialProfileId, open]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!selectedProfile) return;
    onSave({
      profileId: selectedProfile.id,
      profileRevision: selectedProfile.revision,
      modelLabel: selectedProfile.model,
      outputLanguage,
    });
  };

  return (
    <FormDialog open={open} title="总结设置" busy={loading} onClose={onClose}>
      <form className="settings-dialog-form summary-settings-form" onSubmit={submit}>
        {loading ? (
          <div className="summary-settings-skeleton" aria-label="正在读取总结设置">
            <Skeleton />
            <Skeleton className="is-block" />
            <Skeleton />
            <Skeleton className="is-block" />
          </div>
        ) : (
          <>
            <div className="field">
              <label className="field-label" htmlFor="summary-settings-model">
                总结模型
              </label>
              <SelectMenu
                id="summary-settings-model"
                ariaLabel="总结模型"
                value={profileId}
                onChange={setProfileId}
                options={available.map((profile) => ({
                  value: profile.id,
                  label: `${profile.name} · ${profile.model}`,
                }))}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="summary-settings-language">
                输出语言
              </label>
              <SelectMenu
                id="summary-settings-language"
                ariaLabel="输出语言"
                value={outputLanguage}
                onChange={setOutputLanguage}
                options={languageOptions}
              />
            </div>
          </>
        )}
        {!loading && available.length === 0 ? (
          <InlineNotice tone="danger">暂无可用模型</InlineNotice>
        ) : null}
        {error ? <InlineNotice tone="danger">{error}</InlineNotice> : null}
        <div className="actions dialog-actions">
          <button type="button" className="button" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            className="button button-primary"
            disabled={loading || !selectedProfile}
          >
            保存
          </button>
        </div>
      </form>
    </FormDialog>
  );
}

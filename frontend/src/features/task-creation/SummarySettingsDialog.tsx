import { type FormEvent, useEffect, useState } from "react";
import type { ProfileView } from "../../api/types";
import { CloseIcon } from "../../app/icons";
import { FormDialog } from "../../components/FormDialog";
import { SelectMenu } from "../../components/SelectMenu";
import {
  summaryLanguageOptions,
  type SummaryTaskSettings,
} from "../summary-settings/model";

export type { SummaryTaskSettings } from "../summary-settings/model";

export function SummarySettingsDialog({
  open,
  settings,
  profiles,
  onClose,
  onSave,
}: {
  open: boolean;
  settings: SummaryTaskSettings;
  profiles: ProfileView[];
  onClose: () => void;
  onSave: (settings: SummaryTaskSettings) => void;
}) {
  const [draft, setDraft] = useState(settings);

  useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.enabled && !draft.profileId) return;
    onSave(draft);
    onClose();
  };

  return (
    <FormDialog open={open} title="总结设置" onClose={onClose}>
      <button
        type="button"
        className="icon-button summary-settings-close"
        aria-label="关闭总结设置"
        onClick={onClose}
      >
        <CloseIcon />
      </button>

      <form className="summary-settings-dialog-form" onSubmit={submit}>
        <div className="summary-settings-row">
          <label htmlFor="summary-enabled">生成总结</label>
          <label className="settings-switch">
            <input
              id="summary-enabled"
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  enabled: event.target.checked,
                }))
              }
            />
            <span aria-hidden="true" />
          </label>
        </div>

        <div className="summary-settings-row">
          <label htmlFor="summary-profile">总结模型</label>
          <SelectMenu
            id="summary-profile"
            ariaLabel="总结模型"
            value={draft.profileId}
            onChange={(profileId) =>
              setDraft((current) => ({ ...current, profileId }))
            }
            options={
              profiles.length > 0
                ? profiles.map((profile) => ({
                    value: profile.id,
                    label:
                      [profile.name, profile.model].filter(Boolean).join(" · ") ||
                      profile.id,
                  }))
                : [{ value: "", label: "暂无可用模型", disabled: true }]
            }
          />
        </div>

        <div className="summary-settings-row">
          <label htmlFor="summary-language">输出语言</label>
          <SelectMenu
            id="summary-language"
            ariaLabel="输出语言"
            value={draft.outputLanguage}
            onChange={(outputLanguage) =>
              setDraft((current) => ({ ...current, outputLanguage }))
            }
            options={summaryLanguageOptions(draft.outputLanguage)}
          />
        </div>

        {draft.enabled && profiles.length === 0 ? (
          <p className="summary-settings-unavailable" role="status">
            请先在设置中添加并验证总结模型。
          </p>
        ) : null}

        <div className="actions dialog-actions">
          <button type="button" className="button button-quiet" onClick={onClose}>
            取消
          </button>
          <button
            type="submit"
            className="button button-primary"
            disabled={draft.enabled && !draft.profileId}
          >
            保存设置
          </button>
        </div>
      </form>
    </FormDialog>
  );
}

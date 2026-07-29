import { useState } from "react";

export function SecretField({
  id,
  label,
  name,
  hasSecret,
  autoComplete = "off",
}: {
  id: string;
  label: string;
  name: string;
  hasSecret: boolean;
  autoComplete?: string;
}) {
  const [replacing, setReplacing] = useState(!hasSecret);
  return (
    <div className="field secret-field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {replacing ? (
        <div className="secret-edit">
          <input
            id={id}
            name={name}
            className="text-input"
            type="password"
            autoComplete={autoComplete}
            placeholder="输入新凭据"
            required
          />
          {hasSecret && (
            <button
              type="button"
              className="button button-quiet"
              onClick={() => setReplacing(false)}
            >
              取消
            </button>
          )}
        </div>
      ) : (
        <div className="secret-saved">
          <span>已安全保存</span>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => setReplacing(true)}
          >
            替换
          </button>
        </div>
      )}
    </div>
  );
}

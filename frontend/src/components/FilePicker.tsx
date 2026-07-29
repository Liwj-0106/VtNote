import { formatBytes } from "../app/format";

export function FilePicker({
  id,
  accept,
  file,
  limitBytes,
  onChange,
}: {
  id: string;
  accept: string;
  file: File | null;
  limitBytes: number | null;
  onChange: (file: File | null) => void;
}) {
  const tooLarge = Boolean(file && limitBytes !== null && file.size > limitBytes);
  return (
    <div className="file-picker">
      <input
        id={id}
        className="visually-hidden"
        type="file"
        accept={accept}
        onChange={(event) => onChange(event.currentTarget.files?.[0] ?? null)}
      />
      <label className="file-picker-target" htmlFor={id}>
        <span className="file-picker-action">
          {file ? "更换文件" : "选择文件"}
        </span>
        <span className="file-picker-copy">
          {file ? file.name : "从电脑中选择，不会显示本机路径"}
        </span>
      </label>
      {file && (
        <p className={`field-hint ${tooLarge ? "field-error" : ""}`}>
          {formatBytes(file.size)}
          {tooLarge && ` · 超过当前 ${formatBytes(limitBytes!)} 上限`}
        </p>
      )}
    </div>
  );
}

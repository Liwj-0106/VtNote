import { useRef, useState, type DragEvent } from "react";
import { formatBytes } from "../app/format";
import { MotionPresence } from "./MotionPresence";

function acceptsFile(file: File, accept: string) {
  const fileName = file.name.toLowerCase();
  const mimeType = file.type.toLowerCase();

  return accept
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean)
    .some((value) => {
      if (value.startsWith(".")) return fileName.endsWith(value);
      if (value.endsWith("/*")) return mimeType.startsWith(value.slice(0, -1));
      return mimeType === value;
    });
}

export function FilePicker({
  id,
  accept,
  files,
  limitBytes,
  onChange,
}: {
  id: string;
  accept: string;
  files: File[];
  limitBytes: number | null;
  onChange: (files: File[]) => void;
}) {
  const [isDragging, setIsDragging] = useState(false);
  const dragDepth = useRef(0);
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  const oversizedFiles =
    limitBytes === null
      ? []
      : files.filter((file) => file.size > limitBytes);
  const hasFiles = files.length > 0;

  function hasDraggedFiles(event: DragEvent<HTMLLabelElement>) {
    return Array.from(event.dataTransfer.types).includes("Files");
  }

  function handleDragEnter(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    if (!hasDraggedFiles(event)) return;
    dragDepth.current += 1;
    setIsDragging(true);
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setIsDragging(false);
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    dragDepth.current = 0;
    setIsDragging(false);

    const droppedFiles = Array.from(event.dataTransfer.files).filter((file) =>
      acceptsFile(file, accept),
    );
    if (droppedFiles.length > 0) onChange(droppedFiles);
  }

  return (
    <div className="file-picker">
      <input
        id={id}
        className="visually-hidden"
        type="file"
        accept={accept}
        multiple
        onChange={(event) => onChange(Array.from(event.currentTarget.files ?? []))}
      />
      <label
        className={`file-picker-target ${hasFiles ? "has-file" : ""} ${isDragging ? "is-dragging" : ""}`}
        htmlFor={id}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <span className="file-picker-action">
          {hasFiles ? "重新选择" : "上传文件"}
        </span>
        <MotionPresence present={hasFiles}>
          {hasFiles ? (
            <span className="file-picker-copy">
              {files.length === 1 ? files[0].name : `${files.length} 个文件`}
            </span>
          ) : null}
        </MotionPresence>
      </label>
      <MotionPresence present={hasFiles}>
        {hasFiles ? (
        <p className={`field-hint ${oversizedFiles.length > 0 ? "field-error" : ""}`}>
          {formatBytes(totalBytes)}
          {oversizedFiles.length > 0 &&
            ` · ${oversizedFiles.length} 个文件超过 ${formatBytes(limitBytes!)} 上限`}
        </p>
        ) : null}
      </MotionPresence>
    </div>
  );
}

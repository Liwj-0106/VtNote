import { type ReactNode, useId } from "react";
import { ModalDialog } from "./ModalDialog";

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const titleId = useId();

  return (
    <ModalDialog
      open={open}
      busy={busy}
      className="confirm-dialog"
      labelledBy={titleId}
      onClose={onClose}
    >
      <h2 id={titleId}>{title}</h2>
      <div className="dialog-description">{description}</div>
      <div className="actions dialog-actions">
        <button className="button" type="button" disabled={busy} onClick={onClose}>
          返回
        </button>
        <button
          className={`button ${danger ? "button-danger" : "button-primary"}`}
          type="button"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "正在处理…" : confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
}

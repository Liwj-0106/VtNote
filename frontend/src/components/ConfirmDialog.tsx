import { type ReactNode, useEffect, useRef } from "react";

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
  const dialog = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      returnFocus.current = document.activeElement as HTMLElement | null;
      element.showModal();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  return (
    <dialog
      ref={dialog}
      className="confirm-dialog"
      aria-labelledby="confirm-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => returnFocus.current?.focus()}
    >
      <h2 id="confirm-dialog-title">{title}</h2>
      <div className="dialog-description">{description}</div>
      <div className="actions dialog-actions">
        <button className="button" type="button" onClick={onClose}>
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
    </dialog>
  );
}

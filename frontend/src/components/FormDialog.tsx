import { type ReactNode, useId } from "react";
import { ModalDialog } from "./ModalDialog";

export function FormDialog({
  open,
  title,
  className,
  busy = false,
  children,
  onClose,
}: {
  open: boolean;
  title: string;
  className?: string;
  busy?: boolean;
  children: ReactNode;
  onClose: () => void;
}) {
  const titleId = useId();

  return (
    <ModalDialog
      open={open}
      busy={busy}
      className={`confirm-dialog settings-form-dialog${className ? ` ${className}` : ""}`}
      labelledBy={titleId}
      onClose={onClose}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </ModalDialog>
  );
}

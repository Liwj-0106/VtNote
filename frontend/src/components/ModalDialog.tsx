import {
  type ReactNode,
  type RefObject,
  useEffect,
  useRef,
} from "react";
import { MotionPresence } from "./MotionPresence";
import { isDialogBackdropClick } from "./dialogDismissal";

export function ModalDialog({
  open,
  busy = false,
  className = "",
  labelledBy,
  initialFocusRef,
  children,
  onClose,
  onExited,
}: {
  open: boolean;
  busy?: boolean;
  className?: string;
  labelledBy: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
  onClose: () => void;
  onExited?: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnFocusRef.current = document.activeElement as HTMLElement | null;
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      initialFocusRef?.current?.focus();
    } else if (!open && dialog.open && dialog.contains(document.activeElement)) {
      (document.activeElement as HTMLElement | null)?.blur();
    }
  }, [initialFocusRef, open]);

  return (
    <MotionPresence
      present={open}
      variant="dialog"
      onExited={() => {
        const dialog = dialogRef.current;
        if (dialog?.open) {
          if (typeof dialog.close === "function") dialog.close();
          else dialog.removeAttribute("open");
        }
        returnFocusRef.current?.focus();
        onExited?.();
      }}
    >
      <dialog
        ref={dialogRef}
        className={`modal-dialog${className ? ` ${className}` : ""}`}
        aria-labelledby={labelledBy}
        onCancel={(event) => {
          event.preventDefault();
          if (!busy) onClose();
        }}
        onClick={(event) => {
          if (!busy && isDialogBackdropClick(event)) onClose();
        }}
      >
        {children}
      </dialog>
    </MotionPresence>
  );
}

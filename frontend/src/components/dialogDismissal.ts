import type { MouseEvent as ReactMouseEvent } from "react";

export function isDialogBackdropClick(
  event: ReactMouseEvent<HTMLDialogElement>,
): boolean {
  if (event.button !== 0 || event.target !== event.currentTarget) return false;

  const bounds = event.currentTarget.getBoundingClientRect();
  return (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  );
}

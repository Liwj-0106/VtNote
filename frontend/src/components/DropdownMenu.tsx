import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { MotionPresence } from "./MotionPresence";
import { useAnchoredPopover } from "./useAnchoredPopover";

type DropdownMenuSize = "compact" | "default" | "wide";
type DropdownMenuAlign = "start" | "end";

function menuItems(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      '[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"]',
    ),
  ).filter(
    (item) =>
      !item.matches(":disabled") && item.getAttribute("aria-disabled") !== "true",
  );
}

export function DropdownMenu({
  ariaLabel,
  trigger,
  children,
  rootClassName = "",
  triggerClassName = "",
  triggerAriaLabel,
  popoverClassName = "",
  align = "start",
  size = "default",
  disabled = false,
}: {
  ariaLabel: string;
  trigger: ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  rootClassName?: string;
  triggerClassName?: string;
  triggerAriaLabel?: string;
  popoverClassName?: string;
  align?: DropdownMenuAlign;
  size?: DropdownMenuSize;
  disabled?: boolean;
}) {
  const generatedId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const focusOnOpen = useRef<"first" | "last" | null>(null);
  const [open, setOpen] = useState(false);
  const placement = useAnchoredPopover({
    open,
    anchorRef: rootRef,
    popoverRef,
    align,
  });

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  const closeFromAction = useCallback(() => {
    setOpen(false);
  }, []);

  const moveFocus = useCallback((direction: 1 | -1) => {
    const items = menuItems(popoverRef.current);
    if (items.length === 0) return;
    const current = items.indexOf(document.activeElement as HTMLElement);
    const next =
      current < 0
        ? direction === 1
          ? 0
          : items.length - 1
        : (current + direction + items.length) % items.length;
    items[next]?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!rootRef.current?.contains(document.activeElement)) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "Tab") {
        close(false);
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        moveFocus(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      const items = menuItems(popoverRef.current);
      if (event.key === "Home") {
        event.preventDefault();
        items[0]?.focus();
      } else if (event.key === "End") {
        event.preventDefault();
        items.at(-1)?.focus();
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [close, moveFocus, open]);

  useEffect(() => {
    if (!open || focusOnOpen.current === null) return;
    const target = focusOnOpen.current;
    focusOnOpen.current = null;
    const items = menuItems(popoverRef.current);
    (target === "first" ? items[0] : items.at(-1))?.focus();
  }, [open]);

  return (
    <div
      ref={rootRef}
      className={`dropdown-menu${rootClassName ? ` ${rootClassName}` : ""}`}
    >
      <button
        ref={triggerRef}
        id={`${generatedId}-trigger`}
        type="button"
        className={`dropdown-menu-trigger${triggerClassName ? ` ${triggerClassName}` : ""}`}
        aria-label={triggerAriaLabel}
        aria-controls={`${generatedId}-popover`}
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          focusOnOpen.current = event.key === "ArrowDown" ? "first" : "last";
          setOpen(true);
        }}
      >
        {trigger}
      </button>
      <MotionPresence
        present={open}
        variant={placement.opensUp ? "popover-up" : "popover"}
      >
        <div
          ref={popoverRef}
          id={`${generatedId}-popover`}
          className={`dropdown-popover is-${align} is-${size}${placement.opensUp ? " opens-up" : ""}${popoverClassName ? ` ${popoverClassName}` : ""}`}
          role="menu"
          aria-label={ariaLabel}
          style={placement.style}
        >
          {typeof children === "function" ? children(closeFromAction) : children}
        </div>
      </MotionPresence>
    </div>
  );
}

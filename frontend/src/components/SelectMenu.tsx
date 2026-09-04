import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { CheckIcon, ChevronDownIcon } from "../app/icons";
import { MotionPresence } from "./MotionPresence";
import { useAnchoredPopover } from "./useAnchoredPopover";

export interface SelectMenuOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export function SelectMenu({
  id,
  ariaLabel,
  value,
  options,
  onChange,
  className = "",
  required = false,
  disabled = false,
}: {
  id?: string;
  ariaLabel: string;
  value: string;
  options: SelectMenuOption[];
  onChange: (value: string) => void;
  className?: string;
  required?: boolean;
  disabled?: boolean;
}) {
  const generatedId = useId();
  const menuId = `${id ?? generatedId}-menu`;
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const placement = useAnchoredPopover({
    open,
    anchorRef: rootRef,
    popoverRef: listRef,
    maxHeight: 280,
  });

  const selectedIndex = options.findIndex((option) => option.value === value);
  const selectedOption = options[selectedIndex] ?? options[0];
  const enabledIndices = options.flatMap((option, index) =>
    option.disabled ? [] : [index],
  );

  const initialIndex = (preferLast = false) => {
    if (selectedIndex >= 0 && !options[selectedIndex]?.disabled) {
      return selectedIndex;
    }
    return preferLast
      ? (enabledIndices.at(-1) ?? -1)
      : (enabledIndices[0] ?? -1);
  };

  const openMenu = (preferLast = false) => {
    setActiveIndex(initialIndex(preferLast));
    setOpen(true);
  };

  const closeMenu = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  };

  const choose = (index: number) => {
    const option = options[index];
    if (!option || option.disabled) return;
    if (option.value !== value) onChange(option.value);
    closeMenu(true);
  };

  const moveActive = (direction: 1 | -1) => {
    if (enabledIndices.length === 0) return;
    const currentPosition = enabledIndices.indexOf(activeIndex);
    const nextPosition =
      currentPosition < 0
        ? direction === 1
          ? 0
          : enabledIndices.length - 1
        : (currentPosition + direction + enabledIndices.length) %
          enabledIndices.length;
    setActiveIndex(enabledIndices[nextPosition]);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeMenu(true);
      return;
    }
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) {
        openMenu(event.key === "ArrowUp");
      } else {
        moveActive(event.key === "ArrowDown" ? 1 : -1);
      }
      return;
    }
    if (event.key === "Home" && open) {
      event.preventDefault();
      setActiveIndex(enabledIndices[0] ?? -1);
      return;
    }
    if (event.key === "End" && open) {
      event.preventDefault();
      setActiveIndex(enabledIndices.at(-1) ?? -1);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open) choose(activeIndex);
      else openMenu();
    }
  };

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  useEffect(() => {
    if (!open || activeIndex < 0) return;
    const activeOption = listRef.current?.children.item(activeIndex);
    if (activeOption && "scrollIntoView" in activeOption) {
      (activeOption as HTMLElement).scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex, open]);

  return (
    <div
      ref={rootRef}
      className={`select-menu${className ? ` ${className}` : ""}`}
    >
      <button
        ref={triggerRef}
        id={id}
        type="button"
        className="select-menu-trigger"
        role="combobox"
        aria-label={ariaLabel}
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={
          open && activeIndex >= 0 ? `${menuId}-option-${activeIndex}` : undefined
        }
        aria-required={required || undefined}
        disabled={disabled}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={handleKeyDown}
      >
        <span className="select-menu-value">{selectedOption?.label ?? ""}</span>
        <ChevronDownIcon className="select-menu-chevron" />
      </button>

      <MotionPresence
        present={open}
        variant={placement.opensUp ? "popover-up" : "popover"}
      >
        <div
          ref={listRef}
          id={menuId}
          className={`select-menu-popover${placement.opensUp ? " opens-up" : ""}`}
          role="listbox"
          aria-label={ariaLabel}
          style={placement.style}
        >
          {options.map((option, index) => {
            const selected = option.value === value;
            const active = index === activeIndex;
            return (
              <button
                id={`${menuId}-option-${index}`}
                key={option.value}
                type="button"
                className={`select-menu-option${selected ? " is-selected" : ""}${active ? " is-active" : ""}`}
                role="option"
                aria-selected={selected}
                aria-disabled={option.disabled || undefined}
                disabled={option.disabled}
                tabIndex={-1}
                data-active={active || undefined}
                onMouseEnter={() => {
                  if (!option.disabled) setActiveIndex(index);
                }}
                onClick={() => choose(index)}
              >
                <span>{option.label}</span>
                {selected && <CheckIcon className="select-menu-check" />}
              </button>
            );
          })}
        </div>
      </MotionPresence>
    </div>
  );
}

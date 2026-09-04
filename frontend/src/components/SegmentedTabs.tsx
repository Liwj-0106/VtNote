import {
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";

export type SegmentedTabItem<Value extends string> = {
  value: Value;
  label: ReactNode;
  ariaLabel?: string;
  disabled?: boolean;
  panelId?: string;
};

export function segmentedTabId(baseId: string, value: string): string {
  const valueId = value
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "all";
  return `${baseId}-tab-${valueId}`;
}

export function segmentedPanelId(baseId: string, value: string): string {
  const valueId = value
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "all";
  return `${baseId}-panel-${valueId}`;
}

export function SegmentedTabs<Value extends string>({
  id,
  ariaLabel,
  className,
  items,
  value,
  onValueChange,
}: {
  id: string;
  ariaLabel: string;
  className?: string;
  items: readonly SegmentedTabItem<Value>[];
  value: Value;
  onValueChange: (value: Value) => void;
}) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const selectedIndex = items.findIndex((item) => item.value === value && !item.disabled);
  const firstEnabledIndex = items.findIndex((item) => !item.disabled);
  const rovingIndex = selectedIndex >= 0 ? selectedIndex : firstEnabledIndex;

  const moveFocus = (
    event: KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    let nextIndex = currentIndex;
    const key = event.key;

    if (key === "Home") {
      nextIndex = firstEnabledIndex;
    } else if (key === "End") {
      for (let index = items.length - 1; index >= 0; index -= 1) {
        if (!items[index]?.disabled) {
          nextIndex = index;
          break;
        }
      }
    } else if (
      key === "ArrowLeft" ||
      key === "ArrowRight" ||
      key === "ArrowUp" ||
      key === "ArrowDown"
    ) {
      const direction = key === "ArrowLeft" || key === "ArrowUp" ? -1 : 1;
      for (let offset = 1; offset <= items.length; offset += 1) {
        const candidate = (currentIndex + direction * offset + items.length) % items.length;
        if (!items[candidate]?.disabled) {
          nextIndex = candidate;
          break;
        }
      }
    } else {
      return;
    }

    if (nextIndex < 0 || nextIndex === currentIndex) return;
    event.preventDefault();
    const nextItem = items[nextIndex];
    tabRefs.current[nextIndex]?.focus();
    if (nextItem) onValueChange(nextItem.value);
  };

  return (
    <div
      id={id}
      className={className}
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
    >
      {items.map((item, index) => (
        <button
          key={item.value || "all"}
          ref={(element) => {
            tabRefs.current[index] = element;
          }}
          id={segmentedTabId(id, item.value)}
          type="button"
          role="tab"
          aria-label={item.ariaLabel}
          aria-selected={item.value === value}
          aria-controls={item.panelId ?? segmentedPanelId(id, item.value)}
          tabIndex={index === rovingIndex ? 0 : -1}
          className={item.value === value ? "is-selected" : ""}
          disabled={item.disabled}
          onClick={() => onValueChange(item.value)}
          onKeyDown={(event) => moveFocus(event, index)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

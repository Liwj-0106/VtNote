import { CheckIcon } from "../app/icons";

export type SelectionToggleState = "off" | "mixed" | "on";

interface SelectionToggleButtonProps {
  state: SelectionToggleState;
  selectAllLabel: string;
  clearAllLabel: string;
  disabled?: boolean;
  onClick: () => void;
}

export function SelectionToggleButton({
  state,
  selectAllLabel,
  clearAllLabel,
  disabled = false,
  onClick,
}: SelectionToggleButtonProps) {
  const allSelected = state === "on";
  const label = allSelected ? clearAllLabel : selectAllLabel;

  return (
    <button
      type="button"
      className="selection-toggle-button"
      data-state={state}
      aria-pressed={state === "mixed" ? "mixed" : allSelected}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <span className="selection-toggle-mark" aria-hidden="true">
        {allSelected ? <CheckIcon /> : null}
      </span>
    </button>
  );
}

import {
  forwardRef,
  useImperativeHandle,
  useRef,
  type InputHTMLAttributes,
  type KeyboardEvent,
} from "react";
import { CloseIcon, SearchIcon } from "../app/icons";

interface SearchFieldProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "value"> {
  value: string;
  label: string;
  clearLabel?: string;
  onClear: () => void;
}

export const SearchField = forwardRef<HTMLInputElement, SearchFieldProps>(
  function SearchField(
    {
      value,
      label,
      clearLabel = `清除${label}`,
      className = "",
      onClear,
      onKeyDown,
      ...inputProps
    },
    forwardedRef,
  ) {
    const inputRef = useRef<HTMLInputElement>(null);
    useImperativeHandle(forwardedRef, () => inputRef.current as HTMLInputElement);

    const clear = () => {
      onClear();
      inputRef.current?.focus();
    };

    const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape" && value) {
        event.preventDefault();
        clear();
      }
      onKeyDown?.(event);
    };

    return (
      <div className={`search-field${className ? ` ${className}` : ""}`}>
        <SearchIcon />
        <input
          {...inputProps}
          ref={inputRef}
          type="text"
          inputMode="search"
          aria-label={label}
          value={value}
          onKeyDown={handleKeyDown}
        />
        {value ? (
          <button
            type="button"
            className="search-field-clear"
            aria-label={clearLabel}
            onClick={clear}
          >
            <CloseIcon />
          </button>
        ) : null}
      </div>
    );
  },
);

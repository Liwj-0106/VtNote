import { ChevronDownIcon, DownloadIcon } from "../../app/icons";
import { DropdownMenu } from "../../components/DropdownMenu";

export interface ResultDownloadOption {
  label: string;
  description: string;
  action: () => void;
}

export function ResultDownloadMenu({
  onMarkdown,
  onText,
  options,
}: {
  onMarkdown?: () => void;
  onText?: () => void;
  options?: ResultDownloadOption[];
}) {
  const downloadOptions =
    options ??
    [
      { label: "Markdown", description: ".md", action: onMarkdown! },
      { label: "Text", description: ".txt", action: onText! },
    ];

  return (
    <DropdownMenu
      ariaLabel="下载格式"
      align="end"
      size={options ? "wide" : "compact"}
      rootClassName={`result-download-menu${options ? " is-detailed" : ""}`}
      triggerClassName="result-download-trigger"
      popoverClassName="result-download-popover"
      trigger={
        <>
          <DownloadIcon />
          下载
          <ChevronDownIcon className="dropdown-menu-chevron" />
        </>
      }
    >
      {(close) => (
        <>
          {options && <strong>下载</strong>}
          {downloadOptions.map((option) => (
            <button
              type="button"
              role="menuitem"
              key={option.label}
              onClick={() => {
                option.action();
                close();
              }}
            >
              <span>{option.label}</span>
              <small>{option.description}</small>
            </button>
          ))}
        </>
      )}
    </DropdownMenu>
  );
}

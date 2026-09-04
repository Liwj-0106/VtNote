import type { ReactNode, SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({
  children,
  ...props
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  );
}

export const PlusIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M10 3.5v13M3.5 10h13" />
  </IconBase>
);

export const TasksIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M6.5 5h10M6.5 10h10M6.5 15h10" />
    <path d="m2.5 5 .8.8 1.4-1.7M2.5 10l.8.8 1.4-1.7M2.5 15l.8.8 1.4-1.7" />
  </IconBase>
);

export const SettingsIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="10" cy="10" r="2.7" />
    <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4" />
  </IconBase>
);

export const SunIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="10" cy="10" r="3.25" />
    <path d="M10 2v1.5M10 16.5V18M2 10h1.5M16.5 10H18M4.35 4.35l1.05 1.05M14.6 14.6l1.05 1.05M15.65 4.35 14.6 5.4M5.4 14.6l-1.05 1.05" />
  </IconBase>
);

export const MoonIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M16.6 12.5A7 7 0 0 1 7.5 3.4a7 7 0 1 0 9.1 9.1Z" />
  </IconBase>
);

export const SystemThemeIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="15" height="11" rx="2" />
    <path d="M7 17h6M10 14v3" />
  </IconBase>
);

export const SparkIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M10 2.5c.5 3.8 1.7 5 5.5 5.5-3.8.5-5 1.7-5.5 5.5C9.5 9.7 8.3 8.5 4.5 8c3.8-.5 5-1.7 5.5-5.5Z" />
    <path d="M15.5 12.5c.2 1.6.7 2.1 2.3 2.3-1.6.2-2.1.7-2.3 2.3-.2-1.6-.7-2.1-2.3-2.3 1.6-.2 2.1-.7 2.3-2.3Z" />
  </IconBase>
);

type PanelIconProps = IconProps & { direction?: "left" | "right" };

export const PanelIcon = ({ direction = "left", ...props }: PanelIconProps) => (
  <IconBase data-direction={direction} {...props}>
    <rect x="2.5" y="3" width="15" height="14" rx="2" />
    <path
      d={
        direction === "left"
          ? "M7 3v14M11.5 7.5 9 10l2.5 2.5"
          : "M7 3v14M9 7.5l2.5 2.5L9 12.5"
      }
    />
  </IconBase>
);

export const MenuIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M3 5.5h14M3 10h14M3 14.5h14" />
  </IconBase>
);

export const CloseIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m4.5 4.5 11 11M15.5 4.5l-11 11" />
  </IconBase>
);

export const ArrowIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M4 10h12M11.5 5.5 16 10l-4.5 4.5" />
  </IconBase>
);

export const ClipboardIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M7 5V3.8A1.8 1.8 0 0 1 8.8 2h2.4A1.8 1.8 0 0 1 13 3.8V5" />
    <path d="M6 4.5H4.8A1.8 1.8 0 0 0 3 6.3v9.2A2.5 2.5 0 0 0 5.5 18h9a2.5 2.5 0 0 0 2.5-2.5V12" />
    <path d="M7 4.5h6M11.5 10H18M15.5 7.5 18 10l-2.5 2.5" />
  </IconBase>
);

export const SendIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m2.5 3.5 15 6.5-15 6.5 2.3-6.5-2.3-6.5Z" />
    <path d="M4.8 10h8" />
  </IconBase>
);

export const SpinnerIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="10" cy="10" r="6.5" opacity="0.25" />
    <path d="M10 3.5A6.5 6.5 0 0 1 16.5 10" />
  </IconBase>
);

export const LinkIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m8.2 11.8 3.6-3.6" />
    <path d="m6.7 13.3-1.2 1.2a3.1 3.1 0 0 1-4.4-4.4l2.6-2.6a3.1 3.1 0 0 1 4.4 0" />
    <path d="m13.3 6.7 1.2-1.2a3.1 3.1 0 0 1 4.4 4.4l-2.6 2.6a3.1 3.1 0 0 1-4.4 0" />
  </IconBase>
);

export const UploadIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M10 13V3.5M6.5 7 10 3.5 13.5 7" />
    <path d="M3 11.5v3A2.5 2.5 0 0 0 5.5 17h9a2.5 2.5 0 0 0 2.5-2.5v-3" />
  </IconBase>
);

export const BackIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M16 10H4M8.5 5.5 4 10l4.5 4.5" />
  </IconBase>
);

export const ExternalIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M11 3.5h5.5V9M9.5 10.5l7-7" />
    <path d="M16 11v4a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 3 15V5a1.5 1.5 0 0 1 1.5-1.5h4" />
  </IconBase>
);

export const DownloadIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M10 2.5v10M6 9l4 4 4-4M3 16.5h14" />
  </IconBase>
);

export const TrashIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M3.5 5.5h13M7.5 5.5V3.2h5v2.3M5.5 5.5l.7 11h7.6l.7-11" />
    <path d="M8.2 8.5v5M11.8 8.5v5" />
  </IconBase>
);

export const ChevronIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m7 4.5 5.5 5.5L7 15.5" />
  </IconBase>
);

export const ChevronDownIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m4.5 7.5 5.5 5 5.5-5" />
  </IconBase>
);

export const CheckIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m4 10.5 3.5 3.5L16 5.5" />
  </IconBase>
);

export const SearchIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="8.5" cy="8.5" r="5.5" />
    <path d="m12.5 12.5 4 4" />
  </IconBase>
);

export const PlayIcon = (props: IconProps) => (
  <IconBase {...props} fill="currentColor" stroke="none">
    <path d="M6.2 3.9a1 1 0 0 1 1.5-.85l8.2 5.15a1 1 0 0 1 0 1.7l-8.2 5.15a1 1 0 0 1-1.5-.85V3.9Z" />
  </IconBase>
);

export const PauseIcon = (props: IconProps) => (
  <IconBase {...props} fill="currentColor" stroke="none">
    <rect x="5" y="3.5" width="3.4" height="13" rx="1" />
    <rect x="11.6" y="3.5" width="3.4" height="13" rx="1" />
  </IconBase>
);

export const FolderIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M2.5 5.5h5l1.5 2h8.5v7.5a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2V5.5Z" />
    <path d="M2.5 7.5v-2a2 2 0 0 1 2-2h3l1.5 2" />
  </IconBase>
);

export const CollectionIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M3 5.5h4.5l1.5 2h8v7.2a2.3 2.3 0 0 1-2.3 2.3H5.3A2.3 2.3 0 0 1 3 14.7V5.5Z" />
    <path d="M6.5 10h7M6.5 13h5" />
  </IconBase>
);

export const FolderPlusIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M2.5 5.5h5l1.5 2h8.5v7.5a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2V5.5Z" />
    <path d="M12.5 10v4M10.5 12h4" />
  </IconBase>
);

export const EditIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m4 13.5-.7 3.2 3.2-.7L16 6.5 13.5 4 4 13.5Z" />
    <path d="m11.8 5.7 2.5 2.5" />
  </IconBase>
);

export const TagIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M3 3.5h6.5L17 11l-6 6-7.5-7.5V3.5Z" />
    <circle cx="6.7" cy="6.8" r="1" />
  </IconBase>
);

export const BookmarkIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M5 3.5h10v13l-5-3-5 3v-13Z" />
  </IconBase>
);

export const TableIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="15" height="14" rx="2" />
    <path d="M2.5 7.5h15M7.5 3v14" />
  </IconBase>
);

export const GalleryIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="6.25" height="6.25" rx="1.25" />
    <rect x="11.25" y="3" width="6.25" height="6.25" rx="1.25" />
    <rect x="2.5" y="11.75" width="6.25" height="5.25" rx="1.25" />
    <rect x="11.25" y="11.75" width="6.25" height="5.25" rx="1.25" />
  </IconBase>
);

export const WaterfallIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="6.25" height="9" rx="1.25" />
    <rect x="11.25" y="3" width="6.25" height="5.5" rx="1.25" />
    <rect x="2.5" y="14.5" width="6.25" height="2.5" rx="1.25" />
    <rect x="11.25" y="11" width="6.25" height="6" rx="1.25" />
  </IconBase>
);

export const ListIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M7 5h10M7 10h10M7 15h10" />
    <circle cx="3.5" cy="5" r=".75" fill="currentColor" stroke="none" />
    <circle cx="3.5" cy="10" r=".75" fill="currentColor" stroke="none" />
    <circle cx="3.5" cy="15" r=".75" fill="currentColor" stroke="none" />
  </IconBase>
);

export const ColumnsIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="15" height="14" rx="2" />
    <path d="M8 3v14M13 3v14" />
  </IconBase>
);

export const PackageIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m3 6.5 7-4 7 4v7l-7 4-7-4v-7Z" />
    <path d="m3 6.5 7 4 7-4M10 10.5v7M6.5 4.5l7 4" />
  </IconBase>
);

export const MoreIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="4" cy="10" r="1" fill="currentColor" stroke="none" />
    <circle cx="10" cy="10" r="1" fill="currentColor" stroke="none" />
    <circle cx="16" cy="10" r="1" fill="currentColor" stroke="none" />
  </IconBase>
);

export const ShareIcon = (props: IconProps) => (
  <IconBase {...props}>
    <circle cx="5" cy="10" r="2" />
    <circle cx="14.5" cy="5" r="2" />
    <circle cx="14.5" cy="15" r="2" />
    <path d="m6.8 9 5.9-3M6.8 11l5.9 3" />
  </IconBase>
);

export const RefreshIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="M16 7.5A6.5 6.5 0 0 0 4.4 5L3 7.5" />
    <path d="M3 3.5v4h4" />
    <path d="M4 12.5A6.5 6.5 0 0 0 15.6 15l1.4-2.5" />
    <path d="M17 16.5v-4h-4" />
  </IconBase>
);

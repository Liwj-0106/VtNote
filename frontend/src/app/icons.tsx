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

export const PanelIcon = (props: IconProps) => (
  <IconBase {...props}>
    <rect x="2.5" y="3" width="15" height="14" rx="2" />
    <path d="M7 3v14M11.5 7.5 9 10l2.5 2.5" />
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

export const ChevronIcon = (props: IconProps) => (
  <IconBase {...props}>
    <path d="m7 4.5 5.5 5.5L7 15.5" />
  </IconBase>
);

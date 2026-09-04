import type { SVGProps } from "react";

export function BrandMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width="34"
      height="34"
      viewBox="0 0 34 34"
      {...props}
    >
      <rect
        className="brand-mark-surface"
        x="1"
        y="1"
        width="32"
        height="32"
        rx="10"
      />
      <path
        className="brand-mark-glyph"
        d="M8.8 9.6 15.1 23a2.1 2.1 0 0 0 3.8 0l6.3-13.4M22.8 9.6h3"
      />
    </svg>
  );
}

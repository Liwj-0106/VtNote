import type { CSSProperties, ReactNode } from "react";

export function Skeleton({
  className = "",
  style,
}: {
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`skeleton${className ? ` ${className}` : ""}`}
      style={style}
      aria-hidden="true"
    />
  );
}

export function SkeletonStatus({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={`skeleton-status${className ? ` ${className}` : ""}`}
      role="status"
      aria-label={label}
      aria-busy="true"
    >
      {children}
    </div>
  );
}

export function SettingsRowsSkeleton({
  label,
  count = 3,
}: {
  label: string;
  count?: number;
}) {
  return (
    <SkeletonStatus className="settings-skeleton-list" label={label}>
      {Array.from({ length: count }, (_, index) => (
        <section className="settings-row settings-skeleton-row" key={index}>
          <div>
            <Skeleton className="settings-skeleton-heading" />
            <Skeleton className="settings-skeleton-description" />
          </div>
          <Skeleton className="settings-skeleton-value" />
        </section>
      ))}
    </SkeletonStatus>
  );
}

import { AppLink } from "../app/router";

export function EmptyState({
  title,
  description,
  actionLabel,
  actionTo = "/",
}: {
  title: string;
  description?: string;
  actionLabel?: string;
  actionTo?: string;
}) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      {description ? <p>{description}</p> : null}
      {actionLabel && (
        <AppLink className="button" to={actionTo}>
          {actionLabel}
        </AppLink>
      )}
    </div>
  );
}

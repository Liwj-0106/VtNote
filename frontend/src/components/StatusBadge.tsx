import { statusLabel } from "../app/format";

function tone(status: string): string {
  if (status === "completed") return "success";
  if (status === "completed_with_warnings") return "warning";
  if (status === "failed") return "danger";
  if (status === "canceled") return "muted";
  return "active";
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status-badge status-${tone(status)}`}>
      <span className="status-shape" aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}

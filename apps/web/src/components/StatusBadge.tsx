import type { JobStatus } from "@lib/api";

const LABEL: Record<JobStatus, string> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
};

export function StatusBadge({ status }: { status: JobStatus }) {
  return (
    <span className={`badge-${status}`} data-testid="status-badge" data-status={status}>
      {LABEL[status]}
    </span>
  );
}

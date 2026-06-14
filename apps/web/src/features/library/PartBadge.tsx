import type { PartStatus } from "@lib/api";

const STATUS_LABEL: Record<PartStatus, string> = {
  pending: "待核验",
  verified: "已核验",
  rejected: "已拒绝",
};

const SOURCE_LABEL: Record<string, string> = {
  photo: "照片扫描",
  parametric_block: "卡尺测量",
  ar_recognized: "AR 识别",
};

export function StatusBadge({ status }: { status: PartStatus }) {
  return (
    <span className={`badge badge-${status}`} data-testid="part-status-badge" data-status={status}>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function SourceBadge({ source }: { source: string }) {
  return (
    <span className="badge badge-pending" data-testid="part-source-badge" data-source={source}>
      {SOURCE_LABEL[source] ?? source}
    </span>
  );
}

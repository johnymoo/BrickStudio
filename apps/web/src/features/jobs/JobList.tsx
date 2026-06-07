import { Link } from "react-router-dom";
import { useJobStore, selectJobList, type JobRecord } from "@stores/useJobStore";
import { StatusBadge } from "@components/StatusBadge";
import { ProgressBar } from "@components/ProgressBar";
import { formatTimeAgo } from "@lib/format";

export function JobList() {
  const jobs = useJobStore(selectJobList);
  const removeJob = useJobStore((s) => s.removeJob);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-txt-primary">我的积木</h1>
        <span className="text-xs text-txt-secondary">{jobs.length} 个模型</span>
      </div>

      {jobs.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="space-y-2.5">
          {jobs.map((job) => (
            <li key={job.id}>
              <JobRow job={job} onRemove={() => removeJob(job.id)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="card flex flex-col items-center gap-3 py-16 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-accent-bg text-accent">
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="4" y="4" width="6" height="6" rx="1" />
          <rect x="14" y="4" width="6" height="6" rx="1" />
          <rect x="4" y="14" width="6" height="6" rx="1" />
          <rect x="14" y="14" width="6" height="6" rx="1" />
        </svg>
      </div>
      <div>
        <h2 className="text-base font-medium text-txt-primary">还没有积木模型</h2>
        <p className="mt-1 text-sm text-txt-secondary">
          导入一组照片来生成你的第一个 3D 积木模型
        </p>
      </div>
      <Link to="/jobs/new" className="btn-primary mt-2 text-sm">
        导入第一个积木
      </Link>
    </div>
  );
}

interface JobRowProps {
  job: JobRecord;
  onRemove: () => void;
}

function JobRow({ job, onRemove }: JobRowProps) {
  return (
    <div className="card group">
      <div className="flex justify-between items-start mb-1">
        <div>
          <span className="text-sm font-medium text-txt-primary">
            {job.partId.slice(0, 8)}
          </span>
        </div>
        <StatusBadge status={job.status} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-txt-secondary mb-2">
        <span>{job.imageCount} 张照片</span>
        <span>·</span>
        <span>{formatTimeAgo(job.createdAt)}</span>
        {job.stage ? (
          <>
            <span>·</span>
            <span className="truncate">{job.stage}</span>
          </>
        ) : null}
      </div>
      {job.status === "running" && job.progress > 0 ? (
        <div className="mb-2">
          <ProgressBar value={job.progress} />
          <div className="mt-1 text-right text-[10px] text-txt-tertiary">
            {job.progress}% · {job.etaSeconds ? `预计还需 ${Math.ceil(job.etaSeconds / 60)} 分钟` : ""}
          </div>
        </div>
      ) : null}
      {job.error ? <p className="text-xs text-err mb-2">{job.error}</p> : null}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onRemove}
          className="text-xs text-txt-tertiary hover:text-err transition"
          aria-label={`删除任务 ${job.partId}`}
        >
          删除
        </button>
        {job.status === "completed" ? (
          <Link
            to={`/jobs/${job.id}`}
            className="text-xs font-medium text-accent no-underline hover:underline"
          >
            查看模型 →
          </Link>
        ) : (
          <Link
            to={`/jobs/${job.id}`}
            className="text-xs font-medium text-accent no-underline hover:underline"
          >
            查看
          </Link>
        )}
      </div>
    </div>
  );
}

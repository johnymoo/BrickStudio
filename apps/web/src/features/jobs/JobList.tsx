import { Link } from "react-router-dom";
import { useJobStore, selectJobList, type JobRecord } from "@stores/useJobStore";
import { StatusBadge } from "@components/StatusBadge";
import { formatTimeAgo } from "@lib/format";

export function JobList() {
  const jobs = useJobStore(selectJobList);
  const removeJob = useJobStore((s) => s.removeJob);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold text-slate-100">我的任务</h1>
        <span className="text-sm text-slate-400">{jobs.length} 个</span>
      </div>

      {jobs.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="space-y-3">
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
    <div className="card flex flex-col items-center gap-3 py-12 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-slate-800 text-primary-500">
        <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="4" y="4" width="6" height="6" rx="1" />
          <rect x="14" y="4" width="6" height="6" rx="1" />
          <rect x="4" y="14" width="6" height="6" rx="1" />
          <rect x="14" y="14" width="6" height="6" rx="1" />
        </svg>
      </div>
      <div>
        <h2 className="text-lg font-medium text-slate-100">还没有任务</h2>
        <p className="mt-1 text-sm text-slate-400">
          点击右上角 <span className="font-medium text-primary-500">开始新的采集</span> 来上传一组照片
        </p>
      </div>
      <Link to="/capture" className="btn-primary mt-2">
        开始新的采集
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
    <div className="card flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-slate-100">
            {job.partId.slice(0, 8)}
          </span>
          <StatusBadge status={job.status} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
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
        {job.error ? <p className="mt-1 text-xs text-rose-400">{job.error}</p> : null}
      </div>
      <div className="flex items-center gap-2 self-end sm:self-auto">
        <button
          type="button"
          onClick={onRemove}
          className="btn-ghost text-xs"
          aria-label={`删除任务 ${job.partId}`}
        >
          删除
        </button>
        <Link to={`/jobs/${job.id}`} className="btn-primary text-sm">
          查看
        </Link>
      </div>
    </div>
  );
}

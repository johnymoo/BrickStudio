import { useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useJobStore } from "@stores/useJobStore";
import { getJob, subscribeJob, type JobInfo, type JobStreamEvent } from "@lib/api";
import { StatusBadge } from "@components/StatusBadge";
import { ProgressBar } from "@components/ProgressBar";
import { Viewer } from "@features/viewer/Viewer";
import { formatElapsed } from "@lib/format";

const POLL_INTERVAL_MS = 5_000;

export function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const jobId = id ?? "";

  const job = useJobStore((s) => s.jobs[jobId]);
  const updateJob = useJobStore((s) => s.updateJob);
  const addJob = useJobStore((s) => s.addJob);

  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number | null>(null);
  const notifiedRef = useRef<string | null>(null);

  // Tick elapsed counter while the job is running/pending.
  useEffect(() => {
    if (!job || job.status === "completed" || job.status === "failed") return;
    if (startedAtRef.current === null) {
      startedAtRef.current = Date.now();
    }
    const id = window.setInterval(() => {
      if (startedAtRef.current !== null) {
        setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
      }
    }, 1000);
    return () => window.clearInterval(id);
    // We intentionally depend only on `status` — any other change to `job`
    // (progress, stage, …) should not restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);

  // Initial fetch (in case user lands here without a store entry).
  useEffect(() => {
    if (!jobId) {
      setMissing(true);
      return;
    }
    if (job) return; // we already have it locally
    let cancelled = false;
    const ctrl = new AbortController();
    getJob(jobId, ctrl.signal)
      .then((info) => {
        if (cancelled) return;
        addJob(jobFromInfo(info, jobId));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = (err as { status?: number }).status;
        if (status === 404) setMissing(true);
        else setLoadError((err as Error).message ?? "无法加载任务");
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [jobId, job, addJob]);

  // Subscribe to SSE updates + poll fallback.
  useEffect(() => {
    if (!jobId) return;
    if (job?.status === "completed" || job?.status === "failed") return;
    const ctrl = new AbortController();
    const dispose = subscribeJob(jobId, {
      signal: ctrl.signal,
      onEvent: (ev: JobStreamEvent) => handleStreamEvent(ev, updateJob, setStreamError, notifiedRef, jobId),
    });
    const poll = window.setInterval(async () => {
      try {
        const info = await getJob(jobId);
        updateJob(jobId, patchFromInfo(info));
      } catch {
        // swallow polling errors
      }
    }, POLL_INTERVAL_MS);
    return () => {
      ctrl.abort();
      dispose();
      window.clearInterval(poll);
    };
  }, [jobId, job?.status, updateJob]);

  // Native browser notification on terminal states.
  useEffect(() => {
    if (!job) return;
    if (notifiedRef.current === job.id + job.status) return;
    if (job.status !== "completed" && job.status !== "failed") return;
    if (typeof window === "undefined" || !("Notification" in window)) return;
    const fire = () => {
      try {
        const title = job.status === "completed" ? "3D 重建完成" : "3D 重建失败";
        const body =
          job.status === "completed"
            ? `零件 ${job.partId.slice(0, 8)} 已生成`
            : job.error ?? "请稍后重试";
        new Notification(title, { body });
        notifiedRef.current = job.id + job.status;
      } catch {
        // ignore
      }
    };
    if (Notification.permission === "granted") fire();
    else if (Notification.permission !== "denied") {
      Notification.requestPermission()
        .then((perm) => {
          if (perm === "granted") fire();
        })
        .catch(() => undefined);
    }
  }, [job?.status, job?.id, job?.partId, job?.error, job]);

  if (!jobId) return <ErrorState title="任务 ID 缺失" hint="URL 不正确" />;
  if (missing) return <ErrorState title="任务不存在" hint={`本地未找到任务 ${jobId},且后端返回 404`} />;
  if (loadError) return <ErrorState title="加载任务失败" hint={loadError} />;
  if (!job) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10 text-center text-slate-400" data-testid="job-loading">
        加载中…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Link to="/" className="btn-ghost text-sm">
          ← 返回
        </Link>
        <h1 className="text-lg font-medium text-slate-100">任务详情</h1>
        <StatusBadge status={job.status} />
        <span className="text-xs text-slate-500">ID: {job.id.slice(0, 8)}</span>
      </div>

      <div className="card mb-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-slate-300">{job.stage ?? "等待开始"}</span>
          <span className="text-slate-500">已耗时 {formatElapsed(elapsed)}</span>
        </div>
        <ProgressBar value={job.progress} />
        {streamError ? (
          <p className="mt-2 text-xs text-amber-400">
            实时连接异常,已切换为轮询: {streamError}
          </p>
        ) : null}
        {job.error ? <p className="mt-2 text-sm text-rose-400">{job.error}</p> : null}
      </div>

      {job.status === "completed" && job.resultAssetId ? (
        <div className="card">
          <h2 className="mb-3 text-sm font-medium text-slate-300">3D 预览</h2>
          <div className="aspect-square w-full overflow-hidden rounded-lg bg-slate-950 sm:aspect-video">
            <Viewer assetId={job.resultAssetId} />
          </div>
        </div>
      ) : job.status === "running" ? (
        <div className="card text-sm text-slate-400">正在重建中,稍候片刻…</div>
      ) : job.status === "pending" ? (
        <div className="card text-sm text-slate-400">等待调度…</div>
      ) : (
        <div className="card text-sm text-rose-400">任务失败,请检查日志或重试。</div>
      )}
    </div>
  );
}

function ErrorState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mx-auto max-w-2xl px-4 py-12 text-center">
      <h1 className="text-2xl font-semibold text-slate-100">{title}</h1>
      <p className="mt-2 text-sm text-slate-400">{hint}</p>
      <Link to="/" className="btn-primary mt-6 inline-flex">
        返回首页
      </Link>
    </div>
  );
}

function jobFromInfo(info: JobInfo, fallbackId: string): ReturnType<typeof useJobStore.getState>["jobs"][string] {
  const now = new Date().toISOString();
  return {
    id: info.job_id ?? fallbackId,
    captureId: info.capture_id ?? "",
    partId: info.job_id ?? fallbackId,
    status: info.status,
    progress: info.progress,
    stage: info.stage ?? null,
    error: info.error ?? null,
    resultAssetId: info.result_asset_id ?? null,
    createdAt: info.started_at ?? now,
    updatedAt: now,
    imageCount: 0,
  };
}

function patchFromInfo(info: JobInfo): Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]> {
  return {
    status: info.status,
    progress: info.progress,
    stage: info.stage ?? null,
    error: info.error ?? null,
    resultAssetId: info.result_asset_id ?? null,
  };
}

function handleStreamEvent(
  ev: JobStreamEvent,
  updateJob: (id: string, patch: Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]>) => void,
  setStreamError: (msg: string | null) => void,
  notifiedRef: React.MutableRefObject<string | null>,
  jobId: string,
) {
  switch (ev.type) {
    case "progress":
      updateJob(jobId, { progress: ev.progress, stage: ev.stage ?? null });
      break;
    case "stage_change":
      updateJob(jobId, { stage: ev.stage });
      break;
    case "completed":
      updateJob(jobId, {
        status: "completed",
        progress: 100,
        resultAssetId: ev.result_asset_id ?? null,
        stage: "completed",
      });
      break;
    case "failed":
      updateJob(jobId, { status: "failed", error: ev.error, stage: "failed" });
      break;
    case "error":
      setStreamError(ev.message);
      break;
    case "open":
      setStreamError(null);
      break;
    default:
      break;
  }
  // prevent unused-param lint for notifiedRef when running
  void notifiedRef;
}

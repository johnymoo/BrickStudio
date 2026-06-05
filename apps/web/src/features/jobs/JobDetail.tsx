import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import clsx from "clsx";
import { useJobStore } from "@stores/useJobStore";
import { getJob, subscribeJob, type JobInfo, type JobStreamEvent } from "@lib/api";
import { StatusBadge } from "@components/StatusBadge";
import { ProgressBar } from "@components/ProgressBar";
import { Viewer } from "@features/viewer/Viewer";
import { formatElapsed } from "@lib/format";

const POLL_INTERVAL_MS = 5_000;

/**
 * 5-stage visualization (per design-phase2.md §4.1):
 *
 *   📥 下载照片      (downloading_images)             0-5%
 *   📐 稀疏重建      (sparse_reconstruction)         5-30%
 *   🧊 稠密重建      (dense_reconstruction)         30-60%  (COLMAP 才有)
 *   ✨ 网格生成      (mesh_reconstruction + simplification_and_export)  60-95%
 *   ✅ 完成          (completed)                    100%
 *
 * We map arbitrary `stage` strings to a coarse bucket so a new stage name
 * (e.g. `point_cloud_cleaning`) doesn't blow up the UI. Unknown stages
 * slot into the previous known bucket's progress.
 */
type StageKey = "download" | "sparse" | "dense" | "mesh" | "done" | "failed" | "collecting";

interface StageDef {
  key: StageKey;
  emoji: string;
  label: string;
  /** Inclusive lower bound on progress (0-100). */
  min: number;
  /** Inclusive upper bound on progress (0-100). */
  max: number;
}

const STAGES: StageDef[] = [
  { key: "download", emoji: "📥", label: "下载照片", min: 0, max: 5 },
  { key: "sparse", emoji: "📐", label: "稀疏重建", min: 5, max: 30 },
  { key: "dense", emoji: "🧊", label: "稠密重建", min: 30, max: 60 },
  { key: "mesh", emoji: "✨", label: "网格生成", min: 60, max: 95 },
  { key: "done", emoji: "✅", label: "完成", min: 100, max: 100 },
];

const FAILED_STAGE: StageDef = {
  key: "failed",
  emoji: "✗",
  label: "失败",
  min: 0,
  max: 100,
};

const COLLECTING_STAGE: StageDef = {
  key: "collecting",
  emoji: "📷",
  label: "上传中",
  min: 0,
  max: 0,
};

/** Map a raw `stage` string to one of our 5 visualization stages. */
function resolveStage(stage: string | null | undefined, status: string): StageDef {
  if (status === "failed") return FAILED_STAGE;
  if (status === "completed") return STAGES[STAGES.length - 1]!;
  if (stage === "collecting_photos" || !stage) return COLLECTING_STAGE;
  if (stage === "downloading_images") return STAGES[0]!;
  if (stage === "sparse_reconstruction" || stage === "feature_extraction" || stage === "feature_matching") {
    return STAGES[1]!;
  }
  if (
    stage === "dense_reconstruction" ||
    stage === "point_cloud_cleaning" ||
    stage === "stereo_fusion" ||
    stage === "patch_match_stereo"
  ) {
    return STAGES[2]!;
  }
  if (
    stage === "mesh_reconstruction" ||
    stage === "simplification_and_export" ||
    stage === "poisson" ||
    stage === "ball_pivoting"
  ) {
    return STAGES[3]!;
  }
  // Unknown stage — keep whatever we had. Caller will use the previous bucket.
  return STAGES[1]!;
}

function formatEta(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `约 ${Math.max(1, Math.round(seconds))} 秒`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem === 0 ? `约 ${hours} 小时` : `约 ${hours} 小时 ${rem} 分钟`;
}

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
  const [highlight, setHighlight] = useState(false);
  const lastStageRef = useRef<string | null>(null);
  const highlightTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const notifiedRef = useRef<string | null>(null);

  // Clear the highlight ring after ~1.2s (CSS animation duration).
  useEffect(() => {
    if (!highlight) return;
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlight(false);
      highlightTimerRef.current = null;
    }, 1200);
    return () => {
      if (highlightTimerRef.current !== null) {
        window.clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = null;
      }
    };
  }, [highlight]);

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
      onEvent: (ev: JobStreamEvent) =>
        handleStreamEvent(ev, updateJob, setStreamError, notifiedRef, lastStageRef, setHighlight, jobId),
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

  const stageDef = useMemo(() => {
    if (!job) return COLLECTING_STAGE;
    return resolveStage(job.stage ?? null, job.status);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.stage, job?.status]);

  const etaText = useMemo(() => formatEta(job?.etaSeconds), [job?.etaSeconds]);

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
        {job.captureMode ? (
          <span className="text-xs text-slate-500" data-testid="capture-mode-label">
            模式: {job.captureMode}
          </span>
        ) : null}
      </div>

      <div
        className={clsx(
          "card mb-4 transition-colors",
          highlight ? "ring-2 ring-primary-400 stage-highlight" : "",
        )}
        data-testid="stage-card"
        data-stage={stageDef.key}
        data-highlight={highlight ? "true" : "false"}
      >
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
          <span className="text-slate-200" data-testid="stage-label">
            <span aria-hidden className="mr-1">
              {stageDef.emoji}
            </span>
            {stageDef.label}
            {job.stage && stageDef.key !== "failed" ? (
              <span className="ml-2 text-xs text-slate-500">({job.stage})</span>
            ) : null}
          </span>
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span data-testid="elapsed">已耗时 {formatElapsed(elapsed)}</span>
            {etaText ? <span data-testid="eta">{etaText}</span> : null}
          </div>
        </div>

        {/* 5-stage ladder */}
        <ol
          className="mb-3 grid grid-cols-5 gap-1.5"
          data-testid="stage-ladder"
          aria-label="重建阶段"
        >
          {STAGES.map((s) => {
            const reached = job.progress >= s.min;
            const isCurrent = stageDef.key === s.key;
            return (
              <li
                key={s.key}
                className={clsx(
                  "rounded-md border px-2 py-1.5 text-center text-[11px] transition",
                  reached
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                    : "border-slate-700 bg-slate-900/50 text-slate-500",
                  isCurrent ? "ring-1 ring-primary-400" : "",
                )}
                data-testid={`stage-pill-${s.key}`}
                data-active={isCurrent ? "true" : "false"}
                data-reached={reached ? "true" : "false"}
              >
                <span aria-hidden className="mr-0.5">{s.emoji}</span>
                {s.label}
              </li>
            );
          })}
        </ol>

        <ProgressBar value={job.progress} />

        {streamError ? (
          <p className="mt-2 text-xs text-amber-400">
            实时连接异常,已切换为轮询: {streamError}
          </p>
        ) : null}
        {job.error ? <p className="mt-2 text-sm text-rose-400">{job.error}</p> : null}
        {job.pipelineUsed ? (
          <p className="mt-2 text-[11px] text-slate-500" data-testid="pipeline-label">
            pipeline: {job.pipelineUsed}
          </p>
        ) : null}
      </div>

      {job.status === "completed" && job.resultAssetId ? (
        <div className="card">
          <h2 className="mb-3 text-sm font-medium text-slate-300">3D 预览</h2>
          <div className="aspect-square w-full overflow-hidden rounded-lg bg-slate-950 sm:aspect-video">
            <Viewer assetId={job.resultAssetId} />
          </div>
        </div>
      ) : job.status === "running" || job.status === "pending" ? (
        <div className="card text-sm text-slate-400" data-testid="running-card">
          正在重建中,稍候片刻…
        </div>
      ) : (
        <div className="card text-sm text-rose-400" data-testid="failed-card">任务失败,请检查日志或重试。</div>
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
    captureMode: null,
    etaSeconds: info.eta_seconds ?? null,
    pipelineUsed: info.pipeline_used ?? null,
  };
}

function patchFromInfo(info: JobInfo): Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]> {
  const patch: Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]> = {
    status: info.status,
    progress: info.progress,
    stage: info.stage ?? null,
    error: info.error ?? null,
    resultAssetId: info.result_asset_id ?? null,
  };
  if (info.eta_seconds != null) patch.etaSeconds = info.eta_seconds;
  if (info.pipeline_used != null) patch.pipelineUsed = info.pipeline_used;
  return patch;
}

function handleStreamEvent(
  ev: JobStreamEvent,
  updateJob: (id: string, patch: Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]>) => void,
  setStreamError: (msg: string | null) => void,
  notifiedRef: React.MutableRefObject<string | null>,
  lastStageRef: React.MutableRefObject<string | null>,
  setHighlight: (v: boolean) => void,
  jobId: string,
) {
  switch (ev.type) {
    case "progress": {
      const patch: Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]> = {
        progress: ev.progress,
        stage: ev.stage ?? null,
      };
      if (ev.eta_seconds != null) patch.etaSeconds = ev.eta_seconds;
      updateJob(jobId, patch);
      maybeHighlight(ev.stage ?? null, lastStageRef, setHighlight);
      break;
    }
    case "stage_change": {
      const patch: Partial<ReturnType<typeof useJobStore.getState>["jobs"][string]> = {
        stage: ev.stage,
      };
      if (ev.eta_seconds != null) patch.etaSeconds = ev.eta_seconds;
      updateJob(jobId, patch);
      maybeHighlight(ev.stage, lastStageRef, setHighlight);
      break;
    }
    case "completed":
      updateJob(jobId, {
        status: "completed",
        progress: 100,
        resultAssetId: ev.result_asset_id ?? null,
        stage: "completed",
      });
      maybeHighlight("completed", lastStageRef, setHighlight);
      break;
    case "failed":
      updateJob(jobId, { status: "failed", error: ev.error, stage: "failed" });
      maybeHighlight("failed", lastStageRef, setHighlight);
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

function maybeHighlight(
  nextStage: string | null,
  lastStageRef: React.MutableRefObject<string | null>,
  setHighlight: (v: boolean) => void,
) {
  if (!nextStage) return;
  if (lastStageRef.current !== nextStage) {
    lastStageRef.current = nextStage;
    setHighlight(true);
  }
}

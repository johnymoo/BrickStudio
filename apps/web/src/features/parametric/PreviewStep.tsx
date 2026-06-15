/**
 * Step 4 of the parametric-block wizard.
 *
 * Submits the block to the backend, subscribes to the job's SSE stream
 * (reused from `lib/api`), and renders the resulting GLB in the existing
 * `<Viewer>` (R3F). Mirrors the photo path's JobDetail experience
 * (5-stage ladder + ETA) but inlined into the wizard so the user can
 * "tweak the 5 numbers and resubmit" without bouncing through `/jobs/:id`.
 */
import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import { subscribeJob, type JobStreamEvent } from "@lib/api";
import { Viewer } from "@features/viewer/Viewer";
import { ProgressBar } from "@components/ProgressBar";
import { StatusBadge } from "@components/StatusBadge";
import { useJobStore, type JobRecord } from "@stores/useJobStore";
import type { BrickSystem, BrickKind, ParametricBlockResponse } from "./api";

interface PreviewStepProps {
  system: BrickSystem;
  kind: BrickKind;
  unitsX: number;
  unitsY: number;
  partId: string;
  /** Server response from the POST in `ParametricPage`. */
  block: ParametricBlockResponse | null;
  submitError: string | null;
  submitting: boolean;
  /** Lets the user tweak numbers and resubmit. */
  onBack: () => void;
  onRetry: () => void;
  onReset: () => void;
}

interface JobState {
  status: ParametricBlockResponse["status"];
  progress: number;
  stage: string | null;
  error: string | null;
  resultAssetId: string | null;
}

const INITIAL_JOB_STATE: JobState = {
  status: "pending",
  progress: 0,
  stage: null,
  error: null,
  resultAssetId: null,
};

const FIVE_STAGES = [
  { key: "download", emoji: "📥", label: "下载照片" },
  { key: "sparse", emoji: "📐", label: "准备生成" },
  { key: "dense", emoji: "🧊", label: "参数化生成" },
  { key: "mesh", emoji: "✨", label: "网格导出" },
  { key: "done", emoji: "✅", label: "完成" },
];

/**
 * Coarse bucketing for the parametric pipeline — it's a synchronous
 * BlockGenerator so we go from `pending` → `running` (parametric) →
 * `completed` in well under a second, but the SSE event stream still
 * emits the canonical 5 stages. Map them so the ladder animates.
 */
function bucketStage(stage: string | null | undefined): string {
  const s = (stage ?? "").toLowerCase();
  if (s.includes("download") || s.includes("queued")) return "download";
  if (s.includes("sparse") || s.includes("prepare") || s.includes("init")) return "sparse";
  if (s.includes("dense") || s.includes("parametric") || s.includes("generate")) return "dense";
  if (s.includes("mesh") || s.includes("export") || s.includes("upload")) return "mesh";
  if (s.includes("done") || s.includes("complete") || s === "completed") return "done";
  return "sparse";
}

export function PreviewStep({
  system,
  kind,
  unitsX,
  unitsY,
  partId,
  block,
  submitError,
  submitting,
  onBack,
  onRetry,
  onReset,
}: PreviewStepProps) {
  const [jobState, setJobState] = useState<JobState>(INITIAL_JOB_STATE);
  const abortRef = useRef<AbortController | null>(null);
  const updateJob = useJobStore((s) => s.updateJob);

  // Initial seed from the POST response (so we don't need to wait for the
  // SSE open event to show the first progress bar value). The backend's
  // POST 201 always returns ``status: "pending"``; the terminal state
  // arrives via the SSE ``completed`` / ``failed`` event below.
  useEffect(() => {
    if (!block) return;
    setJobState({
      status: block.status,
      progress: block.status === "completed" ? 100 : 0,
      stage: block.status === "completed" ? "completed" : "parametric_generate",
      error: null,
      resultAssetId: null,
    });
  }, [block?.capture_id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Subscribe to SSE updates once we have a job id.
  useEffect(() => {
    const jobId = block?.job_id;
    if (!jobId) return;
    if (jobState.status === "completed" || jobState.status === "failed") return;
    const ctrl = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ctrl;
    const dispose = subscribeJob(jobId, {
      signal: ctrl.signal,
      onEvent: (ev: JobStreamEvent) => {
        applyStreamEvent(ev, setJobState);
        const patch = patchJobFromStreamEvent(ev);
        if (patch) updateJob(jobId, patch);
      },
    });
    return () => {
      ctrl.abort();
      dispose();
    };
    // We intentionally re-subscribe when jobId changes only — internal
    // jobState changes shouldn't re-spawn the EventSource.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block?.job_id, updateJob]);

  // ---- error / loading shells ----------------------------------------------
  if (submitting && !block) {
    return <StatusCard title="提交中…" body="正在把 5 个 caliper 数字发给后端…" testid="submitting-card" />;
  }
  if (submitError) {
    return (
      <div className="card" data-testid="submit-error">
        <h2 className="mb-1 text-sm font-medium text-err">提交失败</h2>
        <p className="mb-3 text-xs text-txt-secondary">{submitError}</p>
        <div className="flex gap-2">
          <button type="button" className="btn-primary rounded-full text-sm" onClick={onRetry} data-testid="retry-btn">
            重试提交
          </button>
          <button type="button" className="btn-outline text-sm" onClick={onBack} data-testid="back-to-measurements">
            ← 回上一步
          </button>
        </div>
      </div>
    );
  }
  if (!block) {
    return <StatusCard title="等待提交…" body="正在准备请求…" testid="pending-submit-card" />;
  }

  // ---- 5-stage ladder + asset preview --------------------------------------
  const stageKey = bucketStage(jobState.stage);
  return (
    <div className="card" data-testid="step-preview">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium text-txt-primary">步骤 4/4 — 预览 GLB</h2>
        <StatusBadge status={jobState.status} />
        <span className="text-[11px] text-txt-tertiary" data-testid="preview-summary">
          {system} · {kind} · {unitsX}×{unitsY}
        </span>
      </div>

      <ol
        className="mb-3 grid grid-cols-5 gap-1.5"
        data-testid="stage-ladder"
        aria-label="生成阶段"
      >
          {FIVE_STAGES.map((s) => {
            const reached = isReached(s.key, stageKey, jobState.status);
            const isCurrent = stageKey === s.key;
            return (
              <li
                key={s.key}
                className={clsx(
                  "rounded-md border px-2 py-1.5 text-center text-[11px] transition",
                  reached
                    ? "border-accent-border bg-accent-bg text-ok"
                    : "border-border bg-card text-txt-tertiary",
                  isCurrent ? "ring-1 ring-accent" : "",
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

      <ProgressBar value={jobState.progress} />

      <p className="mt-2 text-[11px] text-txt-tertiary" data-testid="stage-text">
        阶段: {jobState.stage ?? "—"} · {jobState.progress}%
      </p>

      {jobState.error ? (
        <p className="mt-2 text-xs text-err" data-testid="job-error">错误: {jobState.error}</p>
      ) : null}

      <div className="mt-4">
        {jobState.status === "completed" && jobState.resultAssetId ? (
          <div className="aspect-square w-full overflow-hidden rounded-lg bg-page sm:aspect-video" data-testid="glb-viewer-wrap">
            <Viewer assetId={jobState.resultAssetId} />
          </div>
        ) : jobState.status === "failed" ? (
          <div className="rounded-md border border-err bg-err-bg p-3 text-sm text-err" data-testid="job-failed">
            生成失败, 可调整 5 个 caliper 数字后重试 (例如 1B 错位 0.1 mm 会让反算 stud_Ø 偏 0.05 mm)。
          </div>
        ) : (
          <div className="rounded-md border border-border bg-card p-6 text-center text-sm text-txt-secondary" data-testid="generating">
            正在用 BlockGenerator 拼装 {system} {kind} {unitsX}×{unitsY} · part_id <code className="text-txt-primary">{partId.slice(0, 8)}</code>…
          </div>
        )}
      </div>

      {/* show derived spec when present */}
      {block.derived_spec_mm ? (
        <details className="mt-3" data-testid="derived-spec">
          <summary className="cursor-pointer text-xs text-txt-secondary">推导 spec (server-side)</summary>
          <pre className="mt-1 overflow-x-auto rounded bg-page p-2 text-[11px] text-txt-primary">
            {JSON.stringify(block.derived_spec_mm, null, 2)}
          </pre>
        </details>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button type="button" className="btn-outline text-sm" onClick={onBack} data-testid="preview-back">
            ← 改 5 个数字
          </button>
          {jobState.status === "completed" ? (
            <Link to="/" className="btn-outline text-sm" data-testid="back-to-jobs">
              ← 任务列表
            </Link>
          ) : null}
        </div>
        {jobState.status === "completed" || jobState.status === "failed" ? (
          <button type="button" className="btn-outline text-sm" onClick={onReset} data-testid="start-over">
            ↻ 新建一个
          </button>
        ) : null}
      </div>
    </div>
  );
}

function isReached(stageKey: string, currentKey: string, status: string): boolean {
  const order = ["download", "sparse", "dense", "mesh", "done"];
  if (status === "completed") return true;
  if (status === "failed") return stageKey === "download";
  return order.indexOf(stageKey) <= order.indexOf(currentKey);
}

function applyStreamEvent(
  ev: JobStreamEvent,
  set: Dispatch<SetStateAction<JobState>>,
): void {
  switch (ev.type) {
    case "progress":
      set((s) => ({ ...s, progress: ev.progress, stage: ev.stage ?? s.stage }));
      return;
    case "stage_change":
      set((s) => ({ ...s, stage: ev.stage }));
      return;
    case "completed":
      set((s) => ({
        ...s,
        status: "completed",
        progress: 100,
        stage: "completed",
        resultAssetId: ev.result_asset_id ?? s.resultAssetId,
      }));
      return;
    case "failed":
      set((s) => ({ ...s, status: "failed", error: ev.error }));
      return;
    case "error":
    case "open":
    default:
      return;
  }
}

function patchJobFromStreamEvent(ev: JobStreamEvent): Partial<JobRecord> | null {
  switch (ev.type) {
    case "progress": {
      const patch: Partial<JobRecord> = { progress: ev.progress };
      if (ev.stage !== undefined) patch.stage = ev.stage;
      if (ev.eta_seconds !== undefined) patch.etaSeconds = ev.eta_seconds;
      return patch;
    }
    case "stage_change": {
      const patch: Partial<JobRecord> = { stage: ev.stage };
      if (ev.eta_seconds !== undefined) patch.etaSeconds = ev.eta_seconds;
      return patch;
    }
    case "completed":
      return {
        status: "completed",
        progress: 100,
        stage: "completed",
        resultAssetId: ev.result_asset_id ?? null,
      };
    case "failed":
      return { status: "failed", stage: "failed", error: ev.error };
    case "error":
    case "open":
    default:
      return null;
  }
}

function StatusCard({ title, body, testid }: { title: string; body: string; testid: string }) {
  return (
    <div className="card text-sm text-txt-secondary" data-testid={testid}>
      <p className="text-txt-primary">{title}</p>
      <p className="mt-1 text-xs text-txt-tertiary">{body}</p>
    </div>
  );
}

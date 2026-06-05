import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CAPTURE_MODE_GUIDANCE,
  CAPTURE_MODE_LABELS,
  type CaptureMode,
  createCapture,
  getCapture,
  newPartId,
  ApiClientError,
} from "@lib/api";
import { useJobStore } from "@stores/useJobStore";
import { computeSharpness, type SharpnessLevel, SHARP_THRESHOLD, OK_THRESHOLD } from "@lib/sharpness";
import clsx from "clsx";

/** Per-mode angle guidance. Each entry is a friendly label that the user
 *  sees in the 4-column progress grid. The grid always renders the *current
 *  mode's* guidance — switching modes re-renders the list. */
const ANGLE_GUIDANCE: Record<CaptureMode, Array<{ id: string; label: string; icon: string }>> = {
  phone_walkaround: [
    { id: "pw-h0-0", label: "前 0°", icon: "▢" },
    { id: "pw-h0-45", label: "前 45°", icon: "◫" },
    { id: "pw-h0-90", label: "右 90°", icon: "▤" },
    { id: "pw-h0-135", label: "后 135°", icon: "◆" },
    { id: "pw-h0-180", label: "后 180°", icon: "◐" },
    { id: "pw-h30", label: "高 30°", icon: "◧" },
    { id: "pw-h60", label: "高 60°", icon: "◨" },
    { id: "pw-h90", label: "顶 90°", icon: "▣" },
  ],
  studio_turntable: [
    { id: "st-0", label: "0°", icon: "▢" },
    { id: "st-30", label: "30°", icon: "◫" },
    { id: "st-60", label: "60°", icon: "▤" },
    { id: "st-90", label: "90°", icon: "◆" },
    { id: "st-120", label: "120°", icon: "◐" },
    { id: "st-150", label: "150°", icon: "◧" },
    { id: "st-180", label: "180°", icon: "◨" },
    { id: "st-210", label: "210°", icon: "▣" },
    { id: "st-240", label: "240°", icon: "▢" },
    { id: "st-270", label: "270°", icon: "◫" },
    { id: "st-300", label: "300°", icon: "▤" },
    { id: "st-330", label: "330°", icon: "◆" },
  ],
  quick_snapshot: [
    { id: "qs-1", label: "正面", icon: "▢" },
    { id: "qs-2", label: "侧面", icon: "◫" },
    { id: "qs-3", label: "顶面", icon: "▤" },
    { id: "qs-4", label: "斜角", icon: "◆" },
  ],
};

const MIN_IMAGES = 4;
const MAX_IMAGES = 20; // matches backend `reconstruct_max_images` cap (per design §3.1)

type AngleId = string;

interface CapturedShot {
  id: string;
  blob: Blob;
  url: string;
  angle: AngleId;
  takenAt: number;
  width: number;
  height: number;
}

const SHARPNESS_COLOR: Record<SharpnessLevel, string> = {
  sharp: "bg-emerald-500",
  ok: "bg-amber-400",
  blurry: "bg-rose-500",
};

const SHARPNESS_TEXT: Record<SharpnessLevel, string> = {
  sharp: "✓ 清晰, 可以拍",
  ok: "… 一般, 稳住手机",
  blurry: "✗ 模糊, 调整一下",
};

export function CapturePage() {
  const navigate = useNavigate();
  const addJob = useJobStore((s) => s.addJob);

  const [shots, setShots] = useState<CapturedShot[]>([]);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamLoading, setStreamLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [captureMode, setCaptureMode] = useState<CaptureMode>("quick_snapshot");
  const [sharpness, setSharpness] = useState<SharpnessLevel | null>(null);
  const [sharpnessVariance, setSharpnessVariance] = useState<number>(0);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const sharpnessCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const partId = useMemo(() => newPartId(), []);
  const guidance = ANGLE_GUIDANCE[captureMode];
  const recommended = CAPTURE_MODE_GUIDANCE[captureMode];

  // Start / stop the camera stream.
  useEffect(() => {
    let cancelled = false;
    let localStream: MediaStream | null = null;
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setStreamError("当前环境无摄像头权限 API (需要 HTTPS 或 localhost)");
      setStreamLoading(false);
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then((s) => {
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        localStream = s;
        setStream(s);
        setStreamError(null);
        if (videoRef.current) {
          videoRef.current.srcObject = s;
        }
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setStreamError(err.message || "无法访问摄像头");
      })
      .finally(() => {
        if (!cancelled) setStreamLoading(false);
      });
    return () => {
      cancelled = true;
      if (localStream) localStream.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // Revoke object URLs on unmount.
  useEffect(() => {
    return () => {
      shots.forEach((s) => URL.revokeObjectURL(s.url));
    };
    // We only want to revoke on unmount, not on every shots change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Real-time sharpness monitor. Runs at ~5 Hz (every 200ms) via rAF so it
  // doesn't busy-loop while the camera is paused. Pauses itself when there's
  // no stream or the user is on the file-fallback path.
  useEffect(() => {
    if (!stream) return;
    let last = 0;
    let raf = 0;
    let cancelled = false;
    const tick = (ts: number) => {
      if (cancelled) return;
      if (ts - last >= 200) {
        last = ts;
        const video = videoRef.current;
        // computeSharpness already falls back to SAMPLE_SIZE when the
        // video has no dimensions (jsdom / pre-ready state), so we can
        // call it unconditionally.
        if (video) {
          const r = computeSharpness(video, sharpnessCanvasRef.current);
          setSharpness(r.level);
          setSharpnessVariance(r.variance);
        }
      }
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(raf);
    };
  }, [stream]);

  const nextAngle = useCallback((): AngleId => {
    const used = new Set(shots.map((s) => s.angle));
    const remaining = guidance.map((a) => a.id).find((id) => !used.has(id));
    return remaining ?? guidance[shots.length % guidance.length]!.id;
  }, [shots, guidance]);

  const captureFromVideo = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    // We need a 2D canvas to actually draw the frame to a blob. We re-use a
    // hidden canvas we mount in the JSX below.
    const captureCanvas = document.createElement("canvas");
    if (!video.videoWidth || !video.videoHeight) {
      setStreamError("摄像头尚未就绪,请稍候再试");
      return;
    }
    if (shots.length >= MAX_IMAGES) {
      setStreamError(`最多 ${MAX_IMAGES} 张`);
      return;
    }
    const w = video.videoWidth;
    const h = video.videoHeight;
    captureCanvas.width = w;
    captureCanvas.height = h;
    const ctx = captureCanvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, w, h);
    const blob: Blob = await new Promise((resolve, reject) => {
      captureCanvas.toBlob(
        (b) => (b ? resolve(b) : reject(new Error("toBlob failed"))),
        "image/jpeg",
        0.85,
      );
    });
    const url = URL.createObjectURL(blob);
    const shot: CapturedShot = {
      id: crypto.randomUUID(),
      blob,
      url,
      angle: nextAngle(),
      takenAt: Date.now(),
      width: w,
      height: h,
    };
    setShots((prev) => [...prev, shot]);
  }, [nextAngle, shots.length]);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      const remaining = MAX_IMAGES - shots.length;
      const list = Array.from(files).slice(0, remaining).filter((f) => f.type.startsWith("image/"));
      if (list.length === 0) {
        setStreamError("请选择图片文件");
        return;
      }
      const newShots: CapturedShot[] = list.map((f) => ({
        id: crypto.randomUUID(),
        blob: f,
        url: URL.createObjectURL(f),
        angle: nextAngle(),
        takenAt: Date.now(),
        width: 0,
        height: 0,
      }));
      setShots((prev) => [...prev, ...newShots]);
    },
    [shots.length, nextAngle],
  );

  const removeShot = useCallback((id: string) => {
    setShots((prev) => {
      const target = prev.find((s) => s.id === id);
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((s) => s.id !== id);
    });
  }, []);

  const usedAngles = new Set(shots.map((s) => s.angle));
  const canSubmit = shots.length >= MIN_IMAGES && !submitting;
  const cameraReady = stream !== null && !streamError;

  const onSubmit = useCallback(async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    const fd = new FormData();
    fd.append("part_id", partId);
    fd.append("capture_mode", captureMode);
    shots.forEach((s, i) => fd.append("images", s.blob, `shot-${i + 1}.jpg`));
    try {
      const res = await createCapture(fd, captureMode);
      let jobId: string | undefined = res.job_id;
      if (!jobId) {
        const detail = await getCapture(res.capture_id);
        jobId = detail.job_id;
      }
      addJob({
        id: jobId ?? res.capture_id,
        captureId: res.capture_id,
        partId: res.part_id,
        status: "pending",
        progress: 0,
        stage: "queued",
        error: null,
        resultAssetId: null,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        imageCount: res.image_count,
        captureMode,
      });
      navigate(`/jobs/${jobId ?? res.capture_id}`);
    } catch (err: unknown) {
      if (err instanceof ApiClientError) {
        const body = err.body as { message?: string } | undefined;
        setSubmitError(body?.message ?? err.message);
      } else if (err instanceof Error) {
        setSubmitError(err.message);
      } else {
        setSubmitError("上传失败");
      }
      setSubmitting(false);
    }
  }, [canSubmit, shots, partId, addJob, navigate, captureMode]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">拍照采集</h1>
        <span className="text-xs text-slate-500">
          推荐 {recommended}+ 张 · 已拍 {shots.length}
        </span>
      </div>

      {/* Mode selector — segmented control with 3 options. */}
      <section
        className="card mb-4"
        data-testid="capture-mode-section"
        aria-label="拍照模式"
      >
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-slate-300">拍照模式</h2>
          <span className="text-[10px] text-slate-500">点击切换</span>
        </div>
        <div
          className="grid grid-cols-3 gap-2"
          role="radiogroup"
          aria-label="拍照模式"
        >
          {(Object.keys(ANGLE_GUIDANCE) as CaptureMode[]).map((mode) => {
            const active = mode === captureMode;
            return (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={active}
                data-testid={`capture-mode-${mode}`}
                data-active={active ? "true" : "false"}
                onClick={() => setCaptureMode(mode)}
                className={clsx(
                  "rounded-md border px-2 py-2 text-center text-xs transition",
                  active
                    ? "border-primary-500 bg-primary-500/15 text-primary-100"
                    : "border-slate-700 bg-slate-900/50 text-slate-300 hover:border-slate-500",
                )}
              >
                <span className="block text-base">{CAPTURE_MODE_LABELS[mode].split(" ")[0]}</span>
                <span className="mt-1 block text-[10px] opacity-80">
                  {CAPTURE_MODE_LABELS[mode].replace(/^[^ ]+ /, "")}
                </span>
                <span className="mt-0.5 block text-[10px] text-slate-500">
                  {CAPTURE_MODE_GUIDANCE[mode]} 张
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="card mb-4" data-testid="preview">
        <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg bg-slate-950">
          {cameraReady ? (
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="h-full w-full object-cover"
              data-testid="camera-video"
            />
          ) : (
            <div
              className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-center text-sm text-slate-400"
              data-testid="camera-fallback"
            >
              {streamLoading ? (
                <span>正在请求摄像头权限…</span>
              ) : (
                <>
                  <p className="font-medium text-slate-200">无摄像头 / 权限被拒</p>
                  <p className="text-xs text-slate-500">{streamError}</p>
                  <p className="mt-2 text-xs text-slate-500">
                    请使用下方&ldquo;从相册选择&rdquo;按钮上传图片
                  </p>
                </>
              )}
            </div>
          )}
          {/* Hidden sharpness canvas — reused for the live rAF monitor. */}
          <canvas
            ref={sharpnessCanvasRef}
            className="hidden"
            data-testid="sharpness-canvas"
          />
          {/* Sharpness ring overlay (only when camera is live). */}
          {cameraReady && sharpness ? (
            <div
              className="pointer-events-none absolute right-3 top-3 flex items-center gap-2 rounded-full bg-black/55 px-2.5 py-1 text-[11px] text-white backdrop-blur"
              data-testid="sharpness-indicator"
              data-level={sharpness}
            >
              <span
                aria-hidden
                className={clsx(
                  "h-2.5 w-2.5 rounded-full ring-2 ring-white/30",
                  SHARPNESS_COLOR[sharpness],
                )}
              />
              <span>{SHARPNESS_TEXT[sharpness]}</span>
              <span className="text-[10px] text-slate-300" data-testid="sharpness-variance">
                {Math.round(sharpnessVariance)}
              </span>
            </div>
          ) : null}
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            className="btn-primary"
            data-testid="capture-btn"
            disabled={!cameraReady || shots.length >= MAX_IMAGES}
            onClick={captureFromVideo}
          >
            拍照
          </button>
          <label className="btn-outline cursor-pointer text-sm">
            从相册选择
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              data-testid="file-input"
              className="hidden"
              onChange={(e) => {
                handleFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </section>

      <section className="card mb-4" data-testid="angle-progress">
        <h2 className="mb-2 text-sm font-medium text-slate-300">多角度引导 ({guidance.length})</h2>
        <ul
          className="grid grid-cols-4 gap-2"
          data-testid="angle-grid"
          data-mode={captureMode}
        >
          {guidance.map((a) => {
            const done = usedAngles.has(a.id);
            return (
              <li
                key={a.id}
                className={clsx(
                  "flex flex-col items-center gap-1 rounded-lg border p-2 text-center text-xs transition",
                  done
                    ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                    : "border-slate-700 bg-slate-900/50 text-slate-400",
                )}
                data-testid={`angle-${a.id}`}
                data-done={done ? "true" : "false"}
              >
                <span aria-hidden className="text-lg">
                  {a.icon}
                </span>
                <span>{a.label}</span>
                <span className="text-[10px] opacity-70">{done ? "已拍" : "未拍"}</span>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-slate-500">
          {captureMode === "phone_walkaround"
            ? "围绕物体走, 每 30°-45° 拍一张, 高度 30°/60°/90° 拍 3 张俯角。推荐 8+ 张走 COLMAP 真 SfM 路径。"
            : captureMode === "studio_turntable"
              ? "物体放转盘, 每 30° 拍一张, 共 12 张。最佳 SfM 路径。"
              : "快速 4 张, 走 Open3D fallback 路径 (4-7 张范围)。"}
        </p>
        {/* Expose thresholds to tests / dev tools without leaking the constants to prod. */}
        <p className="sr-only" data-testid="sharpness-thresholds">
          阈值: {OK_THRESHOLD} / {SHARP_THRESHOLD}
        </p>
      </section>

      {shots.length > 0 ? (
        <section className="card mb-4">
          <h2 className="mb-2 text-sm font-medium text-slate-300">已拍照片 ({shots.length})</h2>
          <ul
            className="grid grid-cols-4 gap-2"
            data-testid="shot-grid"
            data-count={shots.length}
          >
            {shots.map((s, i) => (
              <li
                key={s.id}
                className="relative aspect-square overflow-hidden rounded-md border border-slate-700"
                data-testid={`shot-${i + 1}`}
              >
                <img src={s.url} alt={s.angle} className="h-full w-full object-cover" />
                <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
                  {guidance.find((g) => g.id === s.angle)?.label ?? s.angle}
                </span>
                <button
                  type="button"
                  onClick={() => removeShot(s.id)}
                  className="absolute right-1 top-1 grid h-6 w-6 place-items-center rounded-full bg-black/70 text-white shadow ring-1 ring-white/30 hover:bg-rose-500"
                  aria-label={`删除第 ${i + 1} 张照片`}
                  data-testid={`shot-remove-${i + 1}`}
                >
                  {/* Lucide-style X icon. Inline SVG avoids a new dep. */}
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden
                  >
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {submitError ? (
        <div className="card mb-4 border-rose-500/30 bg-rose-500/10 text-sm text-rose-300">
          上传失败: {submitError}
        </div>
      ) : null}

      <button
        type="button"
        className="btn-primary w-full"
        data-testid="submit-btn"
        data-count={shots.length}
        disabled={!canSubmit}
        onClick={onSubmit}
      >
        {submitting
          ? "上传中…"
          : shots.length < MIN_IMAGES
            ? `至少 ${MIN_IMAGES} 张 (${shots.length}/${MIN_IMAGES})`
            : `提交 (${shots.length} 张)`}
      </button>
    </div>
  );
}

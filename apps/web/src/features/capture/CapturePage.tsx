import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createCapture, getCapture, newPartId, ApiClientError } from "@lib/api";
import { useJobStore } from "@stores/useJobStore";
import clsx from "clsx";

const ANGLES = [
  { id: "front", label: "正面", icon: "▢" },
  { id: "side", label: "侧面", icon: "◫" },
  { id: "top", label: "顶面", icon: "▤" },
  { id: "angle", label: "斜角", icon: "◆" },
] as const;

const MIN_IMAGES = 4;
const MAX_IMAGES = 30;

type AngleId = (typeof ANGLES)[number]["id"];

interface CapturedShot {
  id: string;
  blob: Blob;
  url: string;
  angle: AngleId;
  takenAt: number;
  width: number;
  height: number;
}

export function CapturePage() {
  const navigate = useNavigate();
  const addJob = useJobStore((s) => s.addJob);

  const [shots, setShots] = useState<CapturedShot[]>([]);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [streamLoading, setStreamLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const partId = useMemo(() => newPartId(), []);

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

  const nextAngle = useCallback((): AngleId => {
    const used = new Set(shots.map((s) => s.angle));
    const remaining = ANGLES.map((a) => a.id).find((id) => !used.has(id));
    return remaining ?? ANGLES[shots.length % ANGLES.length]!.id;
  }, [shots]);

  const captureFromVideo = useCallback(async () => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
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
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, w, h);
    const blob: Blob = await new Promise((resolve, reject) => {
      canvas.toBlob(
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
      // Best-effort dimension probe; never blocks the state update.
      for (const shot of newShots) {
        readImageDimensions(shot.url)
          .then((dims) => {
            setShots((prev) =>
              prev.map((s) => (s.id === shot.id ? { ...s, width: dims.width, height: dims.height } : s)),
            );
          })
          .catch(() => undefined);
      }
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
    shots.forEach((s, i) => fd.append("images", s.blob, `shot-${i + 1}.jpg`));
    try {
      const res = await createCapture(fd);
      // Refetch capture to find its job_id (the API returns it on the capture
      // object only when one is created, so we follow up).
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
  }, [canSubmit, shots, partId, addJob, navigate]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-xl font-semibold text-slate-100">拍照采集</h1>
        <span className="text-xs text-slate-500">
          至少 {MIN_IMAGES} 张 · 已拍 {shots.length}
        </span>
      </div>

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
                    请使用下方“从相册选择”按钮上传图片
                  </p>
                </>
              )}
            </div>
          )}
          <canvas ref={canvasRef} className="hidden" />
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
        <h2 className="mb-2 text-sm font-medium text-slate-300">多角度引导</h2>
        <ul className="grid grid-cols-4 gap-2">
          {ANGLES.map((a) => {
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
          建议至少覆盖 4 个不同角度。系统会自动为下一张选择未拍过的角度。
        </p>
      </section>

      {shots.length > 0 ? (
        <section className="card mb-4">
          <h2 className="mb-2 text-sm font-medium text-slate-300">已拍照片</h2>
          <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {shots.map((s) => (
              <li
                key={s.id}
                className="relative aspect-square overflow-hidden rounded-md border border-slate-700"
              >
                <img src={s.url} alt={s.angle} className="h-full w-full object-cover" />
                <span className="absolute left-1 top-1 rounded bg-black/60 px-1.5 py-0.5 text-[10px] text-white">
                  {s.angle}
                </span>
                <button
                  type="button"
                  onClick={() => removeShot(s.id)}
                  className="absolute right-1 top-1 grid h-5 w-5 place-items-center rounded-full bg-black/60 text-[10px] text-white hover:bg-rose-500"
                  aria-label={`删除 ${s.angle} 角度照片`}
                >
                  ×
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
        disabled={!canSubmit}
        onClick={onSubmit}
      >
        {submitting ? "上传中…" : shots.length < MIN_IMAGES ? `至少 ${MIN_IMAGES} 张 (${shots.length}/${MIN_IMAGES})` : "上传并开始重建"}
      </button>
    </div>
  );
}

async function readImageDimensions(url: string): Promise<{ width: number; height: number }> {
  // Decode with a hard 1.5s timeout — environments without an image decoder
  // (jsdom) will neither fire onload nor onerror, so we always bail out.
  return new Promise((resolve) => {
    const img = new Image();
    let done = false;
    const finish = (dims: { width: number; height: number }) => {
      if (done) return;
      done = true;
      resolve(dims);
    };
    img.onload = () => finish({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => finish({ width: 0, height: 0 });
    const timer = window.setTimeout(() => finish({ width: 0, height: 0 }), 1500);
    try {
      img.src = url;
    } catch {
      window.clearTimeout(timer);
      finish({ width: 0, height: 0 });
    }
    // If we loaded synchronously, the timer is no-op.
    void timer;
  });
}

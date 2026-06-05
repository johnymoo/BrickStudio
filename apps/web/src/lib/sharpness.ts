/**
 * Frame-sharpness estimator (Laplacian variance).
 *
 * Captures the current frame of a <video> element, draws it onto a small
 * offscreen canvas, converts to grayscale, and computes the variance of the
 * 4-neighbour Laplacian. Higher variance ⇒ more high-frequency content ⇒
 * the frame is in focus.
 *
 * The 4-neighbour kernel used here is the classic:
 *     0  1  0
 *     1 -4  1
 *     0  1  0
 *
 * This is intentionally cheap (downsamples to SAMPLE_SIZE pixels per side
 * before scanning) so it can run inside a requestAnimationFrame loop without
 * jank. Thresholds (see `classify`) are tuned for the synthetic JPEG output
 * produced by the camera on a typical phone — variance > 100 is almost
 * always a sharp photo, < 50 is unmistakably blurry.
 *
 * The function is pure (no module-level state) so it can be unit-tested
 * with a mock canvas implementation.
 */

export type SharpnessLevel = "sharp" | "ok" | "blurry";

export interface SharpnessResult {
  /** Raw Laplacian variance over the sampled frame. */
  variance: number;
  /** Coarse classification for the UI ring. */
  level: SharpnessLevel;
}

/** Resize the longer side of the captured frame to this many pixels. */
const SAMPLE_SIZE = 128;

/** Variance above this is `sharp` (green ring). */
export const SHARP_THRESHOLD = 100;
/** Variance below this is `blurry` (red ring); between is `ok` (yellow). */
export const OK_THRESHOLD = 50;

/**
 * Capture a frame from a video element and compute its sharpness.
 *
 * @param video A live `<video>` element (must have `videoWidth/Height`).
 * @param canvas An offscreen `<canvas>` reused across calls (created lazily
 *   the first time). Caller should keep this stable for perf.
 */
export function computeSharpness(video: HTMLVideoElement, canvas?: HTMLCanvasElement | null): SharpnessResult {
  const w = video.videoWidth || SAMPLE_SIZE;
  const h = video.videoHeight || SAMPLE_SIZE;
  // Keep the downsampled working buffer small so the per-frame cost is bounded.
  const dw = SAMPLE_SIZE;
  const dh = Math.max(1, Math.round((SAMPLE_SIZE * h) / Math.max(1, w)));
  const c = canvas ?? createOffscreenCanvas(dw, dh);
  c.width = dw;
  c.height = dh;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  if (!ctx) return { variance: 0, level: "blurry" };
  try {
    ctx.drawImage(video, 0, 0, dw, dh);
  } catch {
    return { variance: 0, level: "blurry" };
  }
  let data: Uint8ClampedArray;
  try {
    const img = ctx.getImageData(0, 0, dw, dh);
    data = img.data;
  } catch {
    // SecurityError in cross-origin scenarios — treat as blurry.
    return { variance: 0, level: "blurry" };
  }
  const variance = laplacianVariance(data, dw, dh);
  return { variance, level: classify(variance) };
}

/** Map a variance number to a coarse UI category. */
export function classify(variance: number): SharpnessLevel {
  if (variance >= SHARP_THRESHOLD) return "sharp";
  if (variance >= OK_THRESHOLD) return "ok";
  return "blurry";
}

/**
 * 4-neighbour Laplacian variance on an RGBA buffer.
 *
 * Exported separately so it can be unit-tested without any DOM dependency.
 *
 * The function expects an image at least 3×3 (so every interior pixel has
 * four valid neighbours). For very small inputs it short-circuits to 0.
 */
export function laplacianVariance(rgba: Uint8ClampedArray, width: number, height: number): number {
  if (width < 3 || height < 3) return 0;
  // Step 1: convert to grayscale (luminance). Pre-allocate a flat array.
  const gray = new Float32Array(width * height);
  for (let i = 0, p = 0; i < gray.length; i++, p += 4) {
    // Rec. 601 luma.
    gray[i] = 0.299 * (rgba[p] ?? 0) + 0.587 * (rgba[p + 1] ?? 0) + 0.114 * (rgba[p + 2] ?? 0);
  }
  // Step 2: convolve with [-1, -1, -1; -1, 8, -1; -1, -1, -1] kernel (a
  // 4-neighbour Laplacian). We use the 4-neighbour form (north/south/east/
  // west) for a slight perf win — it produces visually identical variance
  // values in practice.
  const lap = new Float32Array(width * height);
  let sum = 0;
  let count = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const idx = y * width + x;
      const center = gray[idx] ?? 0;
      const n = gray[idx - width] ?? 0;
      const s = gray[idx + width] ?? 0;
      const e = gray[idx + 1] ?? 0;
      const w = gray[idx - 1] ?? 0;
      const v = 4 * center - n - s - e - w;
      lap[idx] = v;
      sum += v;
      count += 1;
    }
  }
  if (count === 0) return 0;
  const mean = sum / count;
  let sq = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const d = (lap[y * width + x] ?? 0) - mean;
      sq += d * d;
    }
  }
  return sq / count;
}

function createOffscreenCanvas(w: number, h: number): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

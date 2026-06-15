import { describe, expect, it, vi } from "vitest";
import {
  classify,
  computeSharpness,
  laplacianVariance,
  OK_THRESHOLD,
  SHARP_THRESHOLD,
  type SharpnessLevel,
} from "@lib/sharpness";

/**
 * Build a synthetic RGBA buffer representing a uniform-colour square.
 * A uniform image has zero edge content, so its Laplacian variance must
 * be 0.
 */
function uniformRgba(size: number, grey: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(size * size * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = grey;
    data[i + 1] = grey;
    data[i + 2] = grey;
    data[i + 3] = 255;
  }
  return data;
}

/**
 * Build a synthetic RGBA buffer representing a sharp checkerboard.
 * The 4-neighbour Laplacian of a perfect 2-tone checker is non-zero and
 * saturates well above `SHARP_THRESHOLD`.
 */
function checkerRgba(size: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const v = (Math.floor(x / 2) + Math.floor(y / 2)) % 2 === 0 ? 0 : 255;
      const p = (y * size + x) * 4;
      data[p] = v;
      data[p + 1] = v;
      data[p + 2] = v;
      data[p + 3] = 255;
    }
  }
  return data;
}

/**
 * Build an RGBA buffer with very low-contrast noise — should land in the
 * "ok" / blurry region, not the "sharp" one.
 */
function noisyRgba(size: number, amplitude: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(size * size * 4);
  let seed = 1;
  for (let i = 0; i < data.length; i += 4) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    const v = 128 + (((seed % 256) - 128) * amplitude) / 128;
    const c = Math.max(0, Math.min(255, Math.round(v)));
    data[i] = c;
    data[i + 1] = c;
    data[i + 2] = c;
    data[i + 3] = 255;
  }
  return data;
}

describe("classify", () => {
  it("returns 'sharp' for high variance", () => {
    expect(classify(SHARP_THRESHOLD + 1)).toBe<SharpnessLevel>("sharp");
    expect(classify(5000)).toBe<SharpnessLevel>("sharp");
  });

  it("returns 'ok' for the middle band", () => {
    expect(classify(SHARP_THRESHOLD)).toBe<SharpnessLevel>("sharp");
    expect(classify(SHARP_THRESHOLD - 1)).toBe<SharpnessLevel>("ok");
    expect(classify(OK_THRESHOLD + 1)).toBe<SharpnessLevel>("ok");
    expect(classify(OK_THRESHOLD)).toBe<SharpnessLevel>("ok");
  });

  it("returns 'blurry' for low variance", () => {
    expect(classify(OK_THRESHOLD - 1)).toBe<SharpnessLevel>("blurry");
    expect(classify(0)).toBe<SharpnessLevel>("blurry");
    expect(classify(-100)).toBe<SharpnessLevel>("blurry");
  });
});

describe("laplacianVariance", () => {
  it("is zero for a uniform image (no edges)", () => {
    const v = laplacianVariance(uniformRgba(32, 100), 32, 32);
    expect(v).toBe(0);
  });

  it("is high for a high-contrast checker (lots of edges)", () => {
    const v = laplacianVariance(checkerRgba(32), 32, 32);
    expect(v).toBeGreaterThan(SHARP_THRESHOLD);
  });

  it("classifies uniform / checker / noisy as expected", () => {
    const uniform = laplacianVariance(uniformRgba(32, 100), 32, 32);
    const sharp = laplacianVariance(checkerRgba(32), 32, 32);
    const blurry = laplacianVariance(noisyRgba(32, 1), 32, 32);
    expect(classify(uniform)).toBe<SharpnessLevel>("blurry");
    expect(classify(sharp)).toBe<SharpnessLevel>("sharp");
    // Very-low-amplitude noise shouldn't be classified as sharp.
    expect(classify(blurry)).not.toBe<SharpnessLevel>("sharp");
  });

  it("returns 0 for inputs that are too small to convolve", () => {
    expect(laplacianVariance(new Uint8ClampedArray(4 * 4), 2, 2)).toBe(0);
    expect(laplacianVariance(new Uint8ClampedArray(0), 0, 0)).toBe(0);
  });
});

describe("computeSharpness", () => {
  /**
   * Build a fake 2D canvas context that knows whether the simulated frame
   * should be sharp (high edge content) or blurry (uniform colour). The
   * returned buffer is sized to the actual `w × h` requested by getImageData
   * — computeSharpness downscales to 128×128 internally, so we mirror that
   * here.
   */
  function makeFakeCanvas(expectedVariance: "sharp" | "blurry"): {
    canvas: HTMLCanvasElement;
  } {
    const isSharp = expectedVariance === "sharp";
    const SIDE = 128;
    const buf = new Uint8ClampedArray(SIDE * SIDE * 4);
    for (let i = 0; i < buf.length; i += 4) {
      if (isSharp) {
        const px = (i / 4) % SIDE;
        const py = Math.floor(i / 4 / SIDE);
        const v = (Math.floor(px / 2) + Math.floor(py / 2)) % 2 === 0 ? 0 : 255;
        buf[i] = v;
        buf[i + 1] = v;
        buf[i + 2] = v;
      } else {
        buf[i] = 100;
        buf[i + 1] = 100;
        buf[i + 2] = 100;
      }
      buf[i + 3] = 255;
    }
    const ctx = {
      drawImage: () => undefined,
      getImageData: (_x: number, _y: number, w: number, h: number) => ({
        data: buf,
        width: w,
        height: h,
        colorSpace: "srgb",
      }),
    };
    const canvas = {
      width: SIDE,
      height: SIDE,
      getContext: vi.fn().mockReturnValue(ctx),
    } as unknown as HTMLCanvasElement;
    return { canvas };
  }

  function mockVideo(expectedVariance: "sharp" | "blurry"): {
    video: HTMLVideoElement;
    canvas: HTMLCanvasElement;
  } {
    const { canvas } = makeFakeCanvas(expectedVariance);
    const video = {
      videoWidth: 128,
      videoHeight: 128,
    } as unknown as HTMLVideoElement;
    return { video, canvas };
  }

  it("returns 'blurry' for an out-of-focus frame (uniform colour)", () => {
    const { video, canvas } = mockVideo("blurry");
    const result = computeSharpness(video, canvas);
    expect(result.level).toBe<SharpnessLevel>("blurry");
    expect(result.variance).toBe(0);
  });

  it("returns 'sharp' for a sharp frame (high edge content)", () => {
    const { video, canvas } = mockVideo("sharp");
    const result = computeSharpness(video, canvas);
    expect(result.level).toBe<SharpnessLevel>("sharp");
    expect(result.variance).toBeGreaterThan(SHARP_THRESHOLD);
  });
});

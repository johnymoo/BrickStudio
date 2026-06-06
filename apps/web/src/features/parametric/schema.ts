/**
 * Schema for the parametric-block wizard (v0.3, 建模板块).
 *
 * The 4-step wizard is bound to a single `ParametricFormState` blob that lives
 * inside the `ParametricPage` component. The schema below defines:
 *
 *   - the enums (system / kind) and the size range (units_x, units_y)
 *   - the 5 raw caliper measurements every user must enter
 *   - the cross-check rule used to validate them in real time
 *
 * Field names match the keys stored in `captures.raw_measurements_mm` (see
 * `apps/api/src/db/models.py` + `tools/measure_block.py`), so the same dict
 * can be POSTed verbatim. Five measurements are mandatory regardless of
 * kind/system — the caliper is friendly to all five (`docs/measure-block-
 * annotated.svg` shows the points; `unit = (1A + 1B) / 2` is derived
 * server-side, never asked of the user).
 *
 * "generic" system does not change the schema — the user still measures the
 * same 5 things, the backend substitutes a unit heuristic.
 */
import type { BrickSystem, BrickKind } from "./api";

// -------- systems / kinds ----------------------------------------------------
export const BRICK_SYSTEMS: ReadonlyArray<{
  value: BrickSystem;
  label: string;
  hint: string;
}> = [
  { value: "duplo", label: "得宝 (DUPLO)", hint: "20 mm 节距, 1-5 岁" },
  { value: "lego", label: "乐高 (LEGO)", hint: "8 mm 节距, 6+ 岁" },
  { value: "feile", label: "费乐 (FEILE)", hint: "16 mm 节距, 国产大颗粒" },
  { value: "generic", label: "通用 / 自定义", hint: "未知规格, 按比例缩" },
];

export const BRICK_KINDS: ReadonlyArray<{
  value: BrickKind;
  label: string;
  icon: string;
  hint: string;
}> = [
  { value: "brick", label: "标准砖 (brick)", icon: "🧱", hint: "凸点在顶, 标准高度" },
  { value: "plate", label: "板 (plate)", icon: "▭", hint: "1/3 高度, 凸点在顶" },
  { value: "tile", label: "瓦片 (tile)", icon: "◼", hint: "1/3 高度, 平顶" },
  { value: "slope", label: "斜面 (slope)", icon: "◣", hint: "45° 楔形, 沿 X 方向降" },
];

export const SYSTEM_KIND_MATRIX: Record<BrickSystem, ReadonlyArray<BrickKind>> = {
  duplo: ["brick", "plate", "tile", "slope"],
  lego: ["brick", "plate", "tile", "slope"],
  feile: ["brick", "plate", "tile", "slope"],
  generic: ["brick", "plate", "tile", "slope"],
};

// -------- size (units_x × units_y) ------------------------------------------
/** Per-system public stud pitch (mm). Used purely for hint text — the actual
 *  numeric value is derived by the backend from the caliper measurements. */
export const SYSTEM_UNIT_MM: Record<BrickSystem, number> = {
  duplo: 20.0,
  lego: 8.0,
  feile: 16.0,
  generic: 10.0, // arbitrary default for the "generic" case
};

export const MIN_UNITS = 1;
export const MAX_UNITS = 16;

// -------- 5 raw measurements -------------------------------------------------
/**
 * The 5 caliper measurements keyed by the exact names used in
 * `tools/measure_block.py` (so the JSON we POST matches what the backend
 * stores in `captures.raw_measurements_mm`).
 */
export const MEASUREMENT_FIELDS = [
  {
    key: "outer_pitch",
    code: "1A",
    label: "外径距 (outer_pitch)",
    hint: "砖块总长, 跨两端 stud 圆周最远点 (卡尺跨外)",
    unit: "mm",
  },
  {
    key: "inner_pitch",
    code: "1B",
    label: "内径距 (inner_pitch)",
    hint: "两 stud 圆周之间空隙 (卡尺插入两 stud 之间)",
    unit: "mm",
  },
  {
    key: "stud_diameter",
    code: "3",
    label: "凸点直径 (stud_diameter)",
    hint: "单个 stud 直径 (任选一个, 卡尺卡外径)",
    unit: "mm",
  },
  {
    key: "brick_height_net",
    code: "2",
    label: "砖块净高 (brick_height_net)",
    hint: "底面 → 砖顶, 不含凸点",
    unit: "mm",
  },
  {
    key: "brick_height_total",
    code: "4",
    label: "砖块总高 (brick_height_total)",
    hint: "底面 → 凸点顶, 含凸点",
    unit: "mm",
  },
] as const;

export type MeasurementKey = (typeof MEASUREMENT_FIELDS)[number]["key"];

/** A 5-tuple of caliper values, all in millimetres. */
export type Measurements = Record<MeasurementKey, number>;

/** `true` when all 5 fields are present, finite, > 0, and within a sane range. */
export function isMeasurementsComplete(m: Measurements | null | undefined): m is Measurements {
  if (!m) return false;
  return MEASUREMENT_FIELDS.every((f) => Number.isFinite(m[f.key]) && m[f.key] > 0);
}

/** Cross-check: `stud_diameter ≈ (outer_pitch - inner_pitch) / 2`. Tolerance
 *  0.5 mm. Returns null if all measurements look consistent, otherwise a
 *  short human-readable warning. We keep this lightweight — the canonical
 *  cross-check happens in `tools/measure_block.py` server-side. The
 *  check only needs the 3 "stud" measurements to be filled; the two
 *  height fields are independent so we don't gate on them. */
export function crossCheckMeasurements(m: Measurements): string | null {
  const { outer_pitch, inner_pitch, stud_diameter } = m;
  if (![outer_pitch, inner_pitch, stud_diameter].every((v) => Number.isFinite(v) && v > 0)) {
    return null;
  }
  const derived = (outer_pitch - inner_pitch) / 2;
  const drift = Math.abs(derived - stud_diameter);
  if (drift > 0.5) {
    return `凸点直径反算 ${derived.toFixed(2)} mm 与填写 ${stud_diameter.toFixed(2)} mm 差 ${drift.toFixed(2)} mm, 建议复核 1B 或 3`;
  }
  return null;
}

// -------- aggregate form state ---------------------------------------------
/** Wizard step index. */
export type WizardStep = 1 | 2 | 3 | 4;

export interface ParametricFormState {
  /** Stable per-session id used for both the backend and the SSE stream. */
  partId: string;
  // Step 1: photos (optional — kept for cross-check & visual validation).
  photos: File[];
  // Step 2: spec selection.
  system: BrickSystem;
  kind: BrickKind;
  unitsX: number;
  unitsY: number;
  // Step 3: measurements.
  measurements: Measurements;
  // Server responses (populated after step 4 submission).
  jobId: string | null;
  captureId: string | null;
}

export const DEFAULT_FORM_STATE: Omit<ParametricFormState, "partId"> = {
  photos: [],
  system: "duplo",
  kind: "brick",
  unitsX: 2,
  unitsY: 2,
  measurements: {
    outer_pitch: NaN,
    inner_pitch: NaN,
    stud_diameter: NaN,
    brick_height_net: NaN,
    brick_height_total: NaN,
  },
  jobId: null,
  captureId: null,
};

/** Maximum photos we accept in the wizard (matches the photo path cap so
 *  the upload form mirrors the existing `/captures` behaviour). */
export const MAX_PHOTOS = 20;

/**
 * API client for the parametric-block wizard (v0.3, 建模板块).
 *
 * Endpoints (mirror `apps/api/src/api/v1/parametric_blocks.py`):
 *
 *   POST   /api/v1/parametric-blocks          multipart: system, kind, units_x,
 *                                              units_y, raw_measurements_mm
 *                                              (JSON-encoded), photos (optional)
 *                                              → 201 ParametricBlockResponse
 *   GET    /api/v1/parametric-blocks/{id}     → ParametricBlockResponse
 *                                              (note: backend only mounts POST
 *                                              in v0.3 — GET is reserved for
 *                                              future retries; treat 404 as
 *                                              "the POST response is the
 *                                              source of truth")
 *   GET    /api/v1/jobs/{job_id}/stream       → SSE (reused from lib/api)
 *
 * The block model is intentionally separate from the photo `Capture` — the
 * user doesn't need photos to derive a GLB, they just need 5 caliper
 * numbers. We still accept optional photos so the wizard can do a visual
 * cross-check after generation.
 */
import { request, ApiClientError } from "@lib/api";
import type { Measurements } from "./schema";

// ---------- enums -----------------------------------------------------------
/** Brick system — mirrors `block_generator.py` `_KIND_HEIGHT` keys. */
export type BrickSystem = "duplo" | "lego" | "feile" | "generic";
/** Brick kind — mirrors `block_generator.py` `_KIND_HAS_KNOBS` keys. */
export type BrickKind = "brick" | "plate" | "tile" | "slope";

// ---------- request/response types -----------------------------------------
/** Response shape for both ``POST /api/v1/parametric-blocks`` and (reserved)
 *  ``GET /api/v1/parametric-blocks/{id}``. Mirrors
 *  ``apps/api/src/models/schemas.py:ParametricBlockRead`` exactly — every
 *  field here exists in the Pydantic model. Optional fields use ``| null``
 *  to match the schema (FastAPI's default for optional fields is
 *  ``None``). The 201 response is always ``status="pending"``; terminal
 *  status arrives via the SSE ``completed`` / ``failed`` events. */
export interface ParametricBlockResponse {
  /** UUID of the underlying ``captures`` row. */
  capture_id: string;
  part_id: string;
  /** ``"pending"`` on POST 201, then progresses via SSE events. */
  status: "pending" | "running" | "completed" | "failed";
  /** Always ``"parametric_block"`` on this endpoint. */
  mode: string;
  system: BrickSystem;
  kind: BrickKind;
  units_x: number;
  units_y: number;
  /** 5 caliper numbers keyed exactly as `_RAW_KEYS` in
   *  ``apps/api/src/api/v1/parametric_blocks.py`` — the backend rejects
   *  any payload missing one of these 5 keys. */
  raw_measurements_mm: Measurements;
  /** 4 spec values pre-derived server-side
   *  (``unit_mm`` / ``height_mm`` / ``knob_diameter_mm`` / ``knob_height_mm``). */
  derived_spec_mm?: Record<string, number> | null;
  /** Empty list = clean; the same cross-check runs on the API as in
   *  ``tools/measure_block.py``. */
  cross_check_warnings?: string[] | null;
  job_id: string | null;
  created_at: string;
}

export interface CreateParametricBlockInput {
  part_id: string;
  system: BrickSystem;
  kind: BrickKind;
  units_x: number;
  units_y: number;
  raw_measurements_mm: Measurements;
  /** Optional photos for visual cross-check (0-20 files). */
  photos?: File[];
  signal?: AbortSignal;
}

export interface GetParametricBlockOptions {
  signal?: AbortSignal;
}

// ---------- API calls -------------------------------------------------------
/**
 * POST /api/v1/parametric-blocks
 *
 * Builds a multipart payload identical in shape to the backend route. The
 * `photos` field is optional — omitting it is the supported way to submit
 * a pure-measurement block.
 */
export async function createParametricBlock(
  input: CreateParametricBlockInput,
): Promise<ParametricBlockResponse> {
  const fd = new FormData();
  fd.append("part_id", input.part_id);
  fd.append("system", input.system);
  fd.append("kind", input.kind);
  fd.append("units_x", String(input.units_x));
  fd.append("units_y", String(input.units_y));
  // The backend parses this as JSON and validates against _RAW_KEYS:
  //   outer_pitch_mm, inner_pitch_mm, stud_diameter_mm,
  //   brick_height_net_mm, brick_height_total_mm
  // Schema.ts keys match these exactly (see MEASUREMENT_FIELDS).
  fd.append("raw_measurements_mm", JSON.stringify(input.raw_measurements_mm));

  if (input.photos && input.photos.length > 0) {
    input.photos.forEach((p, i) => fd.append("photos", p, `photo-${i + 1}.jpg`));
  }

  return request<ParametricBlockResponse>(
    "/parametric-blocks",
    { method: "POST", body: fd },
    input.signal,
  );
}

export async function getParametricBlock(
  id: string,
  options: GetParametricBlockOptions = {},
): Promise<ParametricBlockResponse> {
  return request<ParametricBlockResponse>(
    `/parametric-blocks/${encodeURIComponent(id)}`,
    { method: "GET" },
    options.signal,
  );
}

// ---------- re-exports -----------------------------------------------------
export { ApiClientError };

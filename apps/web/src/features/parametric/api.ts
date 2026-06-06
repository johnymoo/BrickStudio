/**
 * API client for the parametric-block wizard (v0.3, 建模板块).
 *
 * Endpoints (mirror `apps/api/src/api/v1/parametric_blocks.py` — integration
 * task to wire the backend):
 *
 *   POST   /api/v1/parametric-blocks          multipart: system, kind, units_x,
 *                                              units_y, raw_measurements_mm
 *                                              (JSON-encoded), images (optional)
 *                                              → 201 ParametricBlockResponse
 *   GET    /api/v1/parametric-blocks/{id}     → ParametricBlockResponse
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
export interface ParametricBlockResponse {
  block_id: string;
  capture_id: string;
  job_id: string;
  part_id: string;
  status: "pending" | "running" | "completed" | "failed";
  system: BrickSystem;
  kind: BrickKind;
  units_x: number;
  units_y: number;
  raw_measurements_mm: Measurements;
  derived_spec_mm?: Record<string, number> | null;
  cross_check_warnings?: string[] | null;
  result_asset_id?: string | null;
  pipeline_used?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
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
  capture_mode?: string;
  signal?: AbortSignal;
}

export interface GetParametricBlockOptions {
  signal?: AbortSignal;
}

// ---------- API calls -------------------------------------------------------
/**
 * POST /api/v1/parametric-blocks
 *
 * Builds a multipart payload identical in shape to the photo path so the
 * backend can dispatch through the same Celery pipeline. The `images`
 * field is optional — omitting it is the supported way to submit a
 * pure-measurement block.
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
  // The backend stores this verbatim into captures.raw_measurements_mm.
  fd.append("raw_measurements_mm", JSON.stringify(input.raw_measurements_mm));
  if (input.capture_mode) fd.append("capture_mode", input.capture_mode);

  if (input.photos && input.photos.length > 0) {
    input.photos.forEach((p, i) => fd.append("images", p, `photo-${i + 1}.jpg`));
  }

  // Reuse lib/api's request() — it correctly handles FormData (no headers,
  // browser sets the multipart boundary). The integration task only needs
  // to mount this exact path on the API side.
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

/**
 * Lightweight typed API client for the BlockTool backend.
 *
 * All paths are prefixed with `/api/v1` (the dev server proxies these to
 * `http://localhost:8000`; in Docker the Caddy proxy strips `/api`).
 */

const API_BASE = "/api/v1";

export type CaptureStatus = "pending" | "running" | "completed" | "failed";
export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface CaptureResponse {
  capture_id: string;
  part_id: string;
  image_count: number;
  status: CaptureStatus;
  job_id?: string;
}

export interface CaptureInfo extends CaptureResponse {
  created_at: string;
  updated_at: string;
}

export interface JobInfo {
  job_id: string;
  status: JobStatus;
  progress: number; // 0..100
  stage?: string | null;
  error?: string | null;
  result_asset_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  capture_id?: string;
  /** Optional ETA in seconds (worker-estimated remaining time). */
  eta_seconds?: number | null;
  /** Pipeline used (colmap_sfm / open3d_fallback / open3d_pure_photogrammetry). */
  pipeline_used?: string | null;
}

export interface AssetInfo {
  asset_id: string;
  kind: "mesh_gltf" | "mesh_obj" | "mesh_stl" | "point_cloud_ply";
  url: string;
  expires_at: string;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export class ApiClientError extends Error {
  public readonly status: number;
  public readonly body: ApiError | unknown;
  constructor(status: number, body: ApiError | unknown, message?: string) {
    super(message ?? (isApiError(body) ? body.message : `HTTP ${status}`));
    this.name = "ApiClientError";
    this.status = status;
    this.body = body;
  }
}

function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    "message" in value &&
    typeof (value as Record<string, unknown>).code === "string" &&
    typeof (value as Record<string, unknown>).message === "string"
  );
}

/**
 * Low-level request helper exposed for feature modules that need to hit a
 * bespoke endpoint not yet wrapped by a typed helper (e.g. the parametric
 * wizard). Prefer the typed helpers above (`createCapture`, `getJob`, …)
 * for the common paths.
 */
export async function request<T>(
  path: string,
  init: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  // For FormData bodies we must NOT touch the headers at all — undici in
  // Node 24 will (incorrectly) treat the body as text/plain and set
  // `content-type: text/plain;charset=UTF-8` if we pass a Headers object
  // alongside. The browser auto-fills `multipart/form-data; boundary=...`
  // for us when we leave headers alone, which is the correct behaviour.
  if (init.body instanceof FormData) {
    const res = await fetch(`${API_BASE}${path}`, { ...init, signal });
    return parseResponse<T>(res);
  }
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, signal });
  return parseResponse<T>(res);
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!res.ok) {
    throw new ApiClientError(res.status, parsed);
  }
  return parsed as T;
}

/**
 * Capture mode — controls the on-device guidance (number of angles, prompts)
 * and tells the backend which reconstruction pipeline is most appropriate.
 * Mirrors `docs/design-phase2.md` §4.1.
 */
export type CaptureMode = "phone_walkaround" | "studio_turntable" | "quick_snapshot" | "parametric_block";

/** Recommended number of photos for each capture mode (frontend guidance). */
export const CAPTURE_MODE_GUIDANCE: Record<CaptureMode, number> = {
  phone_walkaround: 8,
  studio_turntable: 12,
  quick_snapshot: 4,
  parametric_block: 0,
};

export const CAPTURE_MODE_LABELS: Record<CaptureMode, string> = {
  phone_walkaround: "📱 围绕物体走",
  studio_turntable: "🔄 转盘",
  quick_snapshot: "📸 快速拍",
  parametric_block: "参数化建模",
};

/**
 * Upload a multi-part capture (one or more image files) to the backend.
 *
 * @param formData - the multipart payload. Must include `part_id` and at
 *   least one `images` field. Caller is responsible for assembling this.
 * @param captureMode - optional capture mode (forwarded as a form field).
 *   Defaults to `quick_snapshot` server-side.
 */
export async function createCapture(
  formData: FormData,
  captureMode: CaptureMode | string = "quick_snapshot",
  signal?: AbortSignal,
): Promise<CaptureResponse> {
  // Append the mode into the FormData. We mutate the caller's FormData so
  // the call-site doesn't have to remember to wire it up separately. The
  // request() helper below will skip setting Content-Type for FormData,
  // letting the browser / undici set the multipart boundary automatically.
  if (!formData.has("capture_mode")) {
    formData.append("capture_mode", captureMode);
  }
  return request<CaptureResponse>("/captures", { method: "POST", body: formData }, signal);
}

export async function getCapture(id: string, signal?: AbortSignal): Promise<CaptureInfo> {
  return request<CaptureInfo>(`/captures/${encodeURIComponent(id)}`, { method: "GET" }, signal);
}

export async function getJob(id: string, signal?: AbortSignal): Promise<JobInfo> {
  return request<JobInfo>(`/jobs/${encodeURIComponent(id)}`, { method: "GET" }, signal);
}

export async function getAssetUrl(id: string, signal?: AbortSignal): Promise<AssetInfo> {
  return request<AssetInfo>(`/assets/${encodeURIComponent(id)}`, { method: "GET" }, signal);
}

/** Reconstruction stage enum (mirrors `docs/design-phase2.md` §3.2). */
export type JobStage =
  | "collecting_photos"
  | "downloading_images"
  | "sparse_reconstruction"
  | "dense_reconstruction"
  | "point_cloud_cleaning"
  | "mesh_reconstruction"
  | "simplification_and_export"
  | "completed"
  | "failed"
  | (string & {}); // forward-compat: backend may emit new stages

export type JobStreamEvent =
  | { type: "progress"; progress: number; stage?: JobStage | null; eta_seconds?: number | null }
  | { type: "stage_change"; stage: JobStage; eta_seconds?: number | null }
  | { type: "completed"; result_asset_id?: string | null }
  | { type: "failed"; error: string }
  | { type: "open" }
  | { type: "error"; message: string };

export interface SubscribeOptions {
  onEvent: (event: JobStreamEvent) => void;
  signal?: AbortSignal;
}

/**
 * Subscribe to a job's progress via Server-Sent Events.
 *
 * Returns a disposer that closes the underlying EventSource.
 *
 * The EventSource auto-reconnects on transient errors; we surface a single
 * terminal `error` event if the stream closes with a non-recoverable state.
 */
export function subscribeJob(jobId: string, opts: SubscribeOptions): () => void {
  const url = `${API_BASE}/jobs/${encodeURIComponent(jobId)}/stream`;
  const es = new EventSource(url);
  es.addEventListener("open", () => opts.onEvent({ type: "open" }));
  es.addEventListener("progress", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as {
        progress: number;
        stage?: string | null;
        eta_seconds?: number | null;
      };
      opts.onEvent({
        type: "progress",
        progress: data.progress,
        stage: (data.stage ?? null) as JobStage | null,
        eta_seconds: data.eta_seconds ?? null,
      });
    } catch (err) {
      opts.onEvent({ type: "error", message: (err as Error).message });
    }
  });
  es.addEventListener("stage_change", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as {
        stage: string;
        eta_seconds?: number | null;
      };
      opts.onEvent({
        type: "stage_change",
        stage: data.stage as JobStage,
        eta_seconds: data.eta_seconds ?? null,
      });
    } catch (err) {
      opts.onEvent({ type: "error", message: (err as Error).message });
    }
  });
  es.addEventListener("completed", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as { result_asset_id?: string | null };
      opts.onEvent({ type: "completed", result_asset_id: data.result_asset_id });
    } catch {
      opts.onEvent({ type: "completed" });
    } finally {
      es.close();
    }
  });
  es.addEventListener("failed", (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data) as { error: string };
      opts.onEvent({ type: "failed", error: data.error });
    } catch {
      opts.onEvent({ type: "failed", error: "unknown error" });
    } finally {
      es.close();
    }
  });
  es.addEventListener("error", () => {
    opts.onEvent({ type: "error", message: "SSE connection error" });
  });
  const onAbort = () => es.close();
  opts.signal?.addEventListener("abort", onAbort);
  return () => {
    opts.signal?.removeEventListener("abort", onAbort);
    es.close();
  };
}

/** Generate a UUIDv4-ish client-side part identifier. */
export function newPartId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Fallback: not crypto-strong, but adequate as a client identifier.
  return "part-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

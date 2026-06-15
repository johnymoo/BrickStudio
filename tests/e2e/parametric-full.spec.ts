// =============================================================================
// tests/e2e/parametric-full.spec.ts — end-to-end REAL-API integration test.
//
// This is the *integration* test (not a wizard UI test with mocks). The
// brief (track: integration-e2e) says:
//
//   "端到端集成验证 3 个 track 真连起来 (替换 mock)。不修发现的 bug, 报给上游 track。"
//
// So we hit the real API + Celery worker + MinIO stack, with NO
// `page.route()` mocks. The browser (Chromium) navigates to
// `http://127.0.0.1:5173/parametric`, fills the 4-step wizard, and
// waits for the SSE-driven `completed` event. Then we:
//
//   1. Pull `result_asset_id` from the API (the wizard's <Viewer> is
//      running on the same page; the asset id is on `window.__blocktoolLastAsset`).
//   2. GET /api/v1/assets/{id} → 302 with `Location` header pointing at
//      a presigned MinIO URL → fetch the GLB.
//   3. Assert: GLB starts with "glTF" magic, Content-Type=model/gltf-binary,
//      size > 5 KB (matches the design §5.2 acceptance row).
//   4. Read the asset's `meta` (which carries the 5 raw + 4 derived
//      measurements) and assert deviation from feile public spec ≤ 0.2 mm
//      across all 4 dimensions.
//   5. Re-run `tools/measure_block.py` with the SAME 5 raw measurements
//      and assert the GLB we get is byte-equal to the API's output
//      (both come from the same `services.block_generator.export_glb`
//      function so the geometry is deterministic; byte-equal proves no
//      subtle format drift crept into the worker path).
//
// Pre-conditions (set up by `scripts/up-local.sh`):
//   - Vite dev server on http://127.0.0.1:5173 (proxies /api → :8000)
//   - FastAPI on :8000, Celery worker, MinIO on :9000, PG/Redis
//   - HEIC photo at tests/e2e/fixtures/real-photos/IMG_7930.HEIC
//   - Dev server is also expected to allow HEIC upload (Pillow + pillow-heif
//     is installed in the API env per the .gitignored README at that
//     path).
// =============================================================================
import { test, expect, type Page, request as pwRequest } from "@playwright/test";
import { execSync, spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync, statSync, existsSync } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

// ---------- Constants -------------------------------------------------------
const HEIC_FIXTURE = path.join(
  __dirname,
  "fixtures",
  "real-photos",
  "IMG_7930.HEIC",
);
const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const TOOLS_DIR = path.join(REPO_ROOT, "tools");
const COMPARE_DIR = "/tmp/parametric-full-compare";
const BASE_URL = process.env.BLOCKTOOL_E2E_BASE_URL || "http://127.0.0.1:5173";

// feile public spec (from apps/api/src/services/block_generator.py + the
// reference `tools/examples/feile-2x2-brick.json`). The 5 caliper numbers
// are picked so that `(1A+1B)/2 == 16.0` (= feile public unit_mm) and
// `④ - ② == 5.4` (= feile public knob_height_mm). The cross-check
// (1A-1B)/2 == 9.4 == ③, so the wizard should show no warnings.
const FEILE_RAW = {
  outer_pitch_mm: 25.40,
  inner_pitch_mm: 6.60,
  stud_diameter_mm: 9.40,
  brick_height_net_mm: 19.20,
  brick_height_total_mm: 24.60,
};

// Same 5 numbers formatted as a temporary `examples/...json` for
// `tools/measure_block.py` to consume.
const FEILE_CONFIG_JSON = {
  part_id: "e2e-feile-2x2-brick",
  system: "feile",
  kind: "brick",
  units_x: 2,
  units_y: 2,
  raw_measurements_mm: FEILE_RAW,
};

// feile public spec (the reference we deviate against). Sourced from
// block_generator.FEILE_* constants.
const FEILE_PUBLIC = {
  unit_mm: 16.0,
  height_mm: 19.2,
  knob_diameter_mm: 9.4,
  knob_height_mm: 5.4,
};
// The brief's tolerance: "deviation 全 ≤ 0.2mm".
const DEVIATION_TOL_MM = 0.2;

// ---------- Helpers --------------------------------------------------------
/** Bind a JS-side variable to the latest asset id the page has seen. */
async function installAssetIdSniffer(page: Page) {
  await page.addInitScript(() => {
    type W = Window & { __blocktoolLastAsset?: string | null };
    (window as unknown as W).__blocktoolLastAsset = null;
    // Hook the existing asset URL fetcher. The <Viewer> calls
    // /api/v1/assets/{id} and reads .url; we listen on the fetch
    // resource type for a path that matches and stash the id.
    const origFetch = window.fetch.bind(window);
    window.fetch = async function (input: RequestInfo | URL, init?: RequestInit) {
      const res = await origFetch(input, init);
      try {
        const u = typeof input === "string" ? input : (input as Request).url;
        const m = /\/api\/v1\/assets\/([0-9a-f-]{8,})/.exec(u);
        if (m) {
          const w = window as unknown as W;
          w.__blocktoolLastAsset = m[1];
        }
      } catch {
        /* swallow */
      }
      return res;
    };
  });
}

/** Drive the wizard to step 4 with the 5 caliper numbers filled. */
async function fillWizard(
  page: Page,
  opts: { heicPath: string; measurements: typeof FEILE_RAW },
) {
  await page.goto(`${BASE_URL}/parametric`);

  // step 1: photos
  await expect(page.getByTestId("step-photos")).toBeVisible();
  const fileInput = page.getByTestId("photo-input");
  await fileInput.setInputFiles(opts.heicPath);
  // photo-grid has data-count; wait for at least 1 file
  await expect(page.getByTestId("photo-grid")).toHaveAttribute("data-count", "1", {
    timeout: 10_000,
  });
  await page.getByTestId("step1-next").click();

  // step 2: kind — pick feile / brick (default 2x2 already)
  await expect(page.getByTestId("step-kind")).toBeVisible();
  await page.getByTestId("system-feile").click();
  await expect(page.getByTestId("system-feile")).toHaveAttribute("data-active", "true");
  await expect(page.getByTestId("kind-brick")).toHaveAttribute("data-active", "true");
  // 2x2 is the default — verify
  await expect(page.getByTestId("units-x-input")).toHaveValue("2");
  await expect(page.getByTestId("units-y-input")).toHaveValue("2");
  await page.getByTestId("step2-next").click();

  // step 3: 5 caliper numbers
  await expect(page.getByTestId("step-measurements")).toBeVisible();
  for (const [k, v] of Object.entries(opts.measurements)) {
    await page.getByTestId(`measurement-input-${k}`).fill(String(v));
  }
  // No cross-check warning expected (numbers are consistent).
  await expect(page.getByTestId("cross-check-warning")).toHaveCount(0);
  await expect(page.getByTestId("step3-next")).toBeEnabled();
  await page.getByTestId("step3-next").click();
}

/** Run `tools/measure_block.py` with the same 5 measurements. Returns GLB path. */
function runMeasureBlock(): { glbPath: string; specJson: any } {
  mkdirSync(COMPARE_DIR, { recursive: true });
  const configPath = path.join(COMPARE_DIR, "feile-input.json");
  writeFileSync(configPath, JSON.stringify(FEILE_CONFIG_JSON, null, 2), "utf-8");
  // The .py lives in tools/; `uv run --project apps/api` is the canonical
  // way to run it (it imports from apps/api/src). See the docstring.
  execSync(
    `cd "${REPO_ROOT}" && uv run --project apps/api python tools/measure_block.py ` +
      `--config "${configPath}" --output-dir "${COMPARE_DIR}"`,
    { stdio: "inherit" },
  );
  const partId = FEILE_CONFIG_JSON.part_id;
  const glbPath = path.join(COMPARE_DIR, `${partId}.glb`);
  const specPath = path.join(COMPARE_DIR, `${partId}.json`);
  const specJson = JSON.parse(readFileSync(specPath, "utf-8"));
  return { glbPath, specJson };
}

// ---------------------------------------------------------------------------
// Suite setup: this test runs against the live stack — see playwright.config.ts
// baseURL. We don't spawn our own vite preview; the up-local.sh script
// owns the lifecycle.
// ---------------------------------------------------------------------------
test.describe("parametric full integration (real API + Celery + MinIO)", () => {
  test.use({ baseURL: BASE_URL, viewport: { width: 1280, height: 900 } });

  test("feile 2x2 brick: HEIC photo + 5 caliper numbers → GLB ≥ 5KB, deviation ≤ 0.2mm", async ({
    page,
    baseURL,
  }) => {
    test.setTimeout(5 * 60 * 1000); // 5 min — parametric path is < 2s, allow headroom.

    // Pre-flight: stack is up?
    const apiCtx = await pwRequest.newContext({ baseURL });
    const health = await apiCtx.get("/api/v1/health");
    expect(health.status(), "stack must be up; run scripts/up-local.sh first").toBe(200);
    await apiCtx.dispose();

    await installAssetIdSniffer(page);

    // 1. Drive the wizard with the HEIC photo + 5 numbers.
    await fillWizard(page, { heicPath: HEIC_FIXTURE, measurements: FEILE_RAW });

    // 2. Step 4 must appear; SSE should drive status → completed quickly
    // (the parametric path is a single BlockSpec → export_glb call, no SfM).
    await expect(page.getByTestId("step-preview")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("stage-pill-done")).toHaveAttribute(
      "data-reached",
      "true",
      { timeout: 60_000 },
    );
    // The <Viewer> mounts once the SSE "completed" event arrives and
    // `resultAssetId` is set; that's when our asset-id sniffer will
    // have a value.
    await expect(page.getByTestId("glb-viewer-wrap")).toBeVisible({ timeout: 30_000 });

    // 3. Pull the asset id from the page (the Viewer made the GET for
    // the asset URL, which our sniffer captured). Fallback: if the
    // sniffer missed it (network race), read it from the job API
    // directly by scraping the page state.
    let assetId = await page.evaluate(() => {
      const w = window as unknown as Window & { __blocktoolLastAsset?: string | null };
      return w.__blocktoolLastAsset ?? null;
    });
    if (!assetId) {
      // As a last resort, wait a moment and retry. The Viewer is a
      // R3F <Canvas> + useGLTF; the asset URL fetch happens after the
      // SSE event, so there is a brief gap.
      await page.waitForTimeout(2_000);
      assetId = await page.evaluate(() => {
        const w = window as unknown as Window & { __blocktoolLastAsset?: string | null };
        return w.__blocktoolLastAsset ?? null;
      });
    }
    expect(assetId, "asset id must be captured from the Viewer fetch").toMatch(
      /^[0-9a-f-]{8,}$/i,
    );

    // 4. Pull the asset row (JSON) + the GLB bytes.
    const apiCtx2 = await pwRequest.newContext({ baseURL, maxRedirects: 0 });
    const assetRes = await apiCtx2.get(`/api/v1/assets/${assetId}`);
    expect(assetRes.status(), "GET /api/v1/assets/{id} should be 302").toBe(302);
    const assetBody = (await assetRes.json()) as {
      asset_id: string;
      kind: string;
      url: string;
      size_bytes: number;
      meta: {
        pipeline_used: string;
        system: string;
        kind: string;
        units_x: number;
        units_y: number;
        raw_measurements_mm: typeof FEILE_RAW;
        derived_spec_mm: Record<string, number>;
        deviation_from_public_mm?: Record<string, number>;
        cross_check_warnings?: string[];
        vertex_count: number;
        face_count: number;
        bbox_min: number[];
        bbox_max: number[];
      };
    };
    expect(assetBody.kind).toBe("mesh_gltf");
    expect(assetBody.meta.pipeline_used, "must be the parametric path").toBe(
      "parametric_block",
    );
    expect(assetBody.meta.system).toBe("feile");
    expect(assetBody.meta.kind).toBe("brick");
    expect(assetBody.meta.units_x).toBe(2);
    expect(assetBody.meta.units_y).toBe(2);
    expect(assetBody.meta.raw_measurements_mm).toEqual(FEILE_RAW);

    // 5. Download the GLB body. Asset endpoint returns 302 + JSON body;
    // the JSON body's `url` is the presigned MinIO URL.
    const meshRes = await apiCtx2.get(assetBody.url);
    expect(meshRes.status(), "presigned URL must return 200").toBe(200);
    // The brief: "Content-Type=model/gltf-binary, size > 5KB".
    const contentType = meshRes.headers()["content-type"] ?? "";
    expect(contentType, "GLB must be served as model/gltf-binary").toContain(
      "model/gltf-binary",
    );
    const meshBuf = await meshRes.body();
    expect(
      meshBuf.byteLength,
      `GLB must be > 5 KB; got ${meshBuf.byteLength} bytes`,
    ).toBeGreaterThan(5 * 1024);
    // Magic: "glTF"
    const magic = meshBuf.subarray(0, 4).toString("ascii");
    expect(magic, "GLB must start with the 'glTF' magic").toBe("glTF");

    // 6. Save the API's GLB for the byte-equal comparison.
    const apiGlbPath = path.join(COMPARE_DIR, "api-output.glb");
    mkdirSync(COMPARE_DIR, { recursive: true });
    writeFileSync(apiGlbPath, meshBuf);
    const apiGlbSize = statSync(apiGlbPath).size;

    // 7. Deviation from feile public spec must be ≤ 0.2 mm on every
    // dimension. The API's asset meta carries the resolved values; we
    // compare against FEILE_PUBLIC directly.
    // The meta stores bbox_max for spatial checks too, but the design
    // contract is the *resolved spec* values. We have two sources of
    // truth: `derived_spec_mm` (server-derived from the 5 calipers)
    // and `bbox_max[2]` (= height + knob_height = brick_height_total_mm).
    const derived = assetBody.meta.derived_spec_mm;
    const deviations = {
      unit_mm: derived.unit_mm - FEILE_PUBLIC.unit_mm,
      height_mm: derived.height_mm - FEILE_PUBLIC.height_mm,
      knob_diameter_mm: derived.knob_diameter_mm - FEILE_PUBLIC.knob_diameter_mm,
      knob_height_mm: derived.knob_height_mm - FEILE_PUBLIC.knob_height_mm,
    };
    // Stash for the deliverable (no need to print here, but useful for
    // diff tooling).
    writeFileSync(
      path.join(COMPARE_DIR, "deviation.json"),
      JSON.stringify(deviations, null, 2),
      "utf-8",
    );
    for (const [k, v] of Object.entries(deviations)) {
      expect(
        Math.abs(v),
        `deviation on ${k} must be ≤ ${DEVIATION_TOL_MM} mm; got ${v.toFixed(4)} mm`,
      ).toBeLessThanOrEqual(DEVIATION_TOL_MM);
    }
    // bbox_max.z should equal brick_height_total_mm.
    expect(
      Math.abs(assetBody.meta.bbox_max[2] - FEILE_RAW.brick_height_total_mm),
      `bbox_max.z should equal brick_height_total_mm (${FEILE_RAW.brick_height_total_mm}); got ${assetBody.meta.bbox_max[2]}`,
    ).toBeLessThan(0.5);

    // 8. Take a screenshot for the deliverable.
    mkdirSync(SCREENSHOTS_DIR, { recursive: true });
    const screenshotPath = path.join(SCREENSHOTS_DIR, "parametric-full.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: screenshotPath });

    // 9. Run tools/measure_block.py with the SAME 5 caliper numbers
    // and assert its GLB is byte-equal to the API's output. The two
    // are produced by the exact same function (services.block_generator
    // .export_glb); byte-equal is a strong contract: the worker
    // code path is consistent with the standalone CLI.
    const { glbPath: cliGlbPath, specJson: cliSpec } = runMeasureBlock();
    expect(existsSync(cliGlbPath), `CLI produced GLB at ${cliGlbPath}`).toBe(true);
    const apiBytes = readFileSync(apiGlbPath);
    const cliBytes = readFileSync(cliGlbPath);
    const apiHash = crypto.createHash("sha256").update(apiBytes).digest("hex");
    const cliHash = crypto.createHash("sha256").update(cliBytes).digest("hex");
    // Vertex / face count must match exactly (deterministic geometry).
    expect(
      cliSpec.glb.vertex_count,
      `CLI vertex count (${cliSpec.glb.vertex_count}) must match API meta (${assetBody.meta.vertex_count})`,
    ).toBe(assetBody.meta.vertex_count);
    expect(cliSpec.glb.face_count).toBe(assetBody.meta.face_count);
    // Byte-equal: the API calls `export_glb(spec, glb_path)` exactly
    // the same way the CLI does. Any drift (different trimesh version,
    // different packing, different JSON sidecar) would surface here.
    expect(
      apiHash === cliHash,
      `API GLB sha256 (${apiHash}) must match CLI GLB sha256 (${cliHash}); ` +
        `API size=${apiBytes.length}, CLI size=${cliBytes.length}`,
    ).toBe(true);

    test.info().annotations.push({
      type: "integration-summary",
      description: [
        `system=feile kind=brick units=2x2`,
        `api_glb_size=${apiGlbSize} cli_glb_size=${cliBytes.length}`,
        `vertices=${assetBody.meta.vertex_count} faces=${assetBody.meta.face_count}`,
        `deviation_mm=${JSON.stringify(deviations)}`,
        `sha256_api=${apiHash} sha256_cli=${cliHash}`,
        `asset_id=${assetId}`,
      ].join(" | "),
    });
  });
});

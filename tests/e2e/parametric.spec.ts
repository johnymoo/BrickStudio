// =============================================================================
// tests/e2e/parametric.spec.ts — 建模板块 (4-step wizard) end-to-end coverage.
//
// What this covers (per docs/ROADMAP.md §v0.3 / v0.4):
//   1. /parametric page renders, header link navigates
//   2. Step 1 (photos) — optional, can skip
//   3. Step 2 (kind)   — system / kind / size all change the form state
//   4. Step 3 (5 caliper numbers) — submit disabled until all 5 valid
//   5. Step 4 (preview) — POST /api/v1/parametric-blocks hits the API, the
//                         job's SSE stream drives the 5-stage ladder, and the
//                         R3F <Viewer> mounts with the result_asset_id GLB.
//
// This spec follows the same self-contained pattern as
// `tests/e2e/screenshots-capture-ux.spec.ts`: it spins up `pnpm preview` on
// port 4173 for the duration of the describe block, then runs the wizard
// against the static build. We mock the API endpoints with `page.route()` —
// the path strings match the integration contract (api.ts in
// features/parametric), so the production wiring is a one-line replacement:
// drop the route() handler.
//
// The asset endpoint returns a `data:` URL — the viewer just needs a JSON
// body with `url` set; the GLB itself is never rendered. R3F would
// otherwise need a real GLB to mount cleanly.
// =============================================================================
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";
import { execSync } from "node:child_process";

const FIXTURE_PART_ID = "e2e-parametric-001";
const FAKE_JOB_ID = "job-param-e2e-001";
const FAKE_CAPTURE_ID = "capture-param-e2e-001";
const FAKE_ASSET_ID = "asset-param-e2e-001";
const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const REPO_ROOT = path.resolve(__dirname, "..", "..");

/** Stub the parametric API + job stream so the wizard can complete end to end. */
async function mockParametricApi(page: Page) {
  // 1. POST /api/v1/parametric-blocks → 201 with a fake block.
  await page.route("**/api/v1/parametric-blocks", async (route, request) => {
    if (request.method() === "POST") {
      const body = request.postData();
      expect(body, "POST body should be multipart form data").toBeTruthy();
      // The FormData contract:
      //   part_id, system, kind, units_x, units_y, raw_measurements_mm
      // We don't introspect the multipart, but we can hit the URL to
      // confirm at least the path is correct.
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          capture_id: FAKE_CAPTURE_ID,
          job_id: FAKE_JOB_ID,
          part_id: FIXTURE_PART_ID,
          status: "running",
          mode: "parametric_block",
          system: "duplo",
          kind: "brick",
          units_x: 2,
          units_y: 2,
          raw_measurements_mm: {
            outer_pitch_mm: 36,
            inner_pitch_mm: 4,
            stud_diameter_mm: 16,
            brick_height_net_mm: 17,
            brick_height_total_mm: 24,
          },
          derived_spec_mm: { unit_mm: 20, height_mm: 17, knob_diameter_mm: 16, knob_height_mm: 7 },
          cross_check_warnings: [],
          created_at: "2026-06-06T10:00:00.000Z",
        }),
      });
      return;
    }
    if (request.method() === "GET") {
      // GET /api/v1/parametric-blocks/{id} — return completed with a fake asset.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          capture_id: FAKE_CAPTURE_ID,
          job_id: FAKE_JOB_ID,
          part_id: FIXTURE_PART_ID,
          status: "completed",
          system: "duplo",
          kind: "brick",
          units_x: 2,
          units_y: 2,
          raw_measurements_mm: {
            outer_pitch_mm: 36,
            inner_pitch_mm: 4,
            stud_diameter_mm: 16,
            brick_height_net_mm: 17,
            brick_height_total_mm: 24,
          },
          created_at: "2026-06-06T10:00:00.000Z",
        }),
      });
      return;
    }
    await route.continue();
  });

  // 2. GET /api/v1/jobs/{id} → completed (used as a fallback if SSE never opens).
  await page.route(`**/api/v1/jobs/${FAKE_JOB_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job_id: FAKE_JOB_ID,
        capture_id: FAKE_CAPTURE_ID,
        kind: "parametric_block",
        status: "completed",
        progress: 100,
        stage: "completed",
        error: null,
        result_asset_id: FAKE_ASSET_ID,
        started_at: "2026-06-06T10:00:00.000Z",
        finished_at: "2026-06-06T10:00:01.000Z",
        eta_seconds: null,
        pipeline_used: "parametric_block",
      }),
    });
  });

  // 3. GET /api/v1/jobs/{id}/stream → SSE that emits a `completed` event.
  // The lib/api's subscribeJob uses EventSource, which doesn't allow custom
  // headers, so we route by URL and return a text/event-stream response.
  await page.route(`**/api/v1/jobs/${FAKE_JOB_ID}/stream`, async (route) => {
    const body = [
      "event: open\n",
      "data: {}\n",
      "\n",
      "event: progress\n",
      `data: ${JSON.stringify({ progress: 50, stage: "parametric_generate" })}\n`,
      "\n",
      "event: completed\n",
      `data: ${JSON.stringify({ result_asset_id: FAKE_ASSET_ID })}\n`,
      "\n",
    ].join("");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
      body,
    });
  });

  // 4. GET /api/v1/assets/{id} → JSON pointing at a tiny inline GLB fixture.
  // The asset_id in the URL is the one the SSE emitted above, but the
  // path contains it — Playwright's glob is good enough.
  await page.route(`**/api/v1/assets/${FAKE_ASSET_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset_id: FAKE_ASSET_ID,
        kind: "mesh_gltf",
        url: "data:application/octet-stream;base64,Z2xURg==", // "glTF" base64
        expires_at: "2026-06-07T10:00:00.000Z",
      }),
    });
  });
}

test.describe("parametric wizard", () => {
  // Run against the same self-contained preview server the screenshot
  // suite uses (port 4173). The spec assumes `apps/web/dist` is already
  // built — the brief says pnpm test:e2e runs without docker compose.
  test.use({
    baseURL: "http://localhost:4173",
    viewport: { width: 1280, height: 900 },
  });

  let previewProc: ReturnType<typeof execSync> | null = null;
  let startedPreview = false;

  test.beforeAll(async () => {
    // lsof exits non-zero when nothing is listening on 4173, so guard the
    // call with try/catch instead of letting the throw abort the suite.
    let alreadyUp = false;
    try {
      const lsof = execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" });
      alreadyUp = lsof.includes("LISTEN");
    } catch {
      alreadyUp = false;
    }
    if (!alreadyUp) {
      const { spawn } = await import("node:child_process");
      const proc = spawn(
        "pnpm",
        ["--filter", "@blocktool/web", "exec", "vite", "preview", "--port", "4173", "--strictPort"],
        { cwd: REPO_ROOT, stdio: "ignore", detached: true },
      );
      previewProc = proc as unknown as ReturnType<typeof execSync>;
      startedPreview = true;
      const start = Date.now();
      while (Date.now() - start < 15_000) {
        let bound = false;
        try {
          const out = execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" });
          bound = out.includes("LISTEN");
        } catch {
          bound = false;
        }
        if (bound) break;
        await new Promise((r) => setTimeout(r, 250));
      }
    }
  });

  test.afterAll(async () => {
    if (startedPreview && previewProc && "pid" in previewProc) {
      try {
        process.kill(-((previewProc as unknown as { pid: number }).pid), "SIGTERM");
      } catch {
        /* already gone */
      }
    }
  });

  test("header link navigates to /parametric", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("header-parametric-link")).toBeVisible();
    await page.getByTestId("header-parametric-link").click();
    await expect(page).toHaveURL(/\/parametric$/);
    await expect(page.getByRole("heading", { name: "建模板块" })).toBeVisible();
  });

  test("4-step wizard: skip photos → pick system/kind/size → enter 5 caliper numbers → submit → preview", async ({ page }) => {
    await mockParametricApi(page);
    await page.goto("/parametric");
    await expect(page.getByTestId("step-photos")).toBeVisible();

    // ---- step 1: skip photos ----
    await page.getByTestId("step1-next").click();
    await expect(page.getByTestId("step-kind")).toBeVisible();

    // ---- step 2: change system/kind/units ----
    await expect(page.getByTestId("system-duplo")).toHaveAttribute("data-active", "true");
    await page.getByTestId("system-lego").click();
    await expect(page.getByTestId("system-lego")).toHaveAttribute("data-active", "true");
    await page.getByTestId("kind-tile").click();
    await expect(page.getByTestId("kind-tile")).toHaveAttribute("data-active", "true");
    // units_x: 2 → 3 via the + button
    await page.getByTestId("units-x-inc").click();
    await expect(page.getByTestId("units-x-input")).toHaveValue("3");
    // step2 summary assertion (read via form-state hidden block)
    await expect(page.getByTestId("form-system")).toHaveText("lego");
    await expect(page.getByTestId("form-kind")).toHaveText("tile");
    await expect(page.getByTestId("form-units-x")).toHaveText("3");
    await page.getByTestId("step2-next").click();
    await expect(page.getByTestId("step-measurements")).toBeVisible();

    // ---- step 3: 5 caliper numbers ----
    const next3 = page.getByTestId("step3-next");
    await expect(next3).toBeDisabled();
    // embed-like diagram rendered
    await expect(page.getByTestId("measurement-diagram")).toBeVisible();
    // The wizard's input testids carry the `_mm` suffix (the keys are the
    // same names we POST in raw_measurements_mm, and the backend
    // ``_RAW_KEYS`` in parametric_blocks.py rejects any payload missing
    // them — see apps/api/src/api/v1/parametric_blocks.py:107-113).
    const measurements: Record<string, number> = {
      outer_pitch_mm: 16.0,
      inner_pitch_mm: 7.2,
      stud_diameter_mm: 4.4,
      brick_height_net_mm: 9.5,
      brick_height_total_mm: 11.2,
    };
    for (const [k, v] of Object.entries(measurements)) {
      await page.getByTestId(`measurement-input-${k}`).fill(String(v));
    }
    await expect(next3).toBeEnabled();

    // ---- step 4: submit + preview ----
    await next3.click();
    await expect(page.getByTestId("step-preview")).toBeVisible({ timeout: 15_000 });
    // Stage ladder appears + final "done" pill becomes reached.
    await expect(page.getByTestId("stage-ladder")).toBeVisible();
    // The mock returned status=completed + the SSE emits a `completed` event,
    // so the "done" pill must be reached. Allow some time for the SSE-driven
    // setState to flush.
    await expect(page.getByTestId("stage-pill-done")).toHaveAttribute("data-reached", "true", { timeout: 10_000 });
    // The Viewer mounts when resultAssetId is set.
    await expect(page.getByTestId("glb-viewer-wrap")).toBeVisible();
    // And the viewer-level chrome.
    await expect(page.getByTestId("viewer")).toBeVisible();

    // Capture a screenshot for the deliverable.
    const out = path.join(SCREENSHOTS_DIR, "parametric-preview.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });

  test("submit error is surfaced as a banner, no preview", async ({ page }) => {
    // Same mocks but make the POST fail with a 422.
    await page.route("**/api/v1/parametric-blocks", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "PARAMETRIC_INVALID", message: "units_x out of range", details: {} },
          }),
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/parametric");
    await page.getByTestId("step1-next").click();
    await page.getByTestId("step2-next").click();
    await expect(page.getByTestId("step-measurements")).toBeVisible();
    for (const [k, v] of Object.entries({
      outer_pitch_mm: 20,
      inner_pitch_mm: 4,
      stud_diameter_mm: 8,
      brick_height_net_mm: 9.6,
      brick_height_total_mm: 11.3,
    })) {
      await page.getByTestId(`measurement-input-${k}`).fill(String(v));
    }
    await page.getByTestId("step3-next").click();
    // The error banner appears; the wizard stays on step 3 so the user
    // can fix the inputs and retry.
    await expect(page.getByTestId("submit-error-banner")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("submit-error-banner")).toContainText(/units_x/);
    // Preview never rendered.
    await expect(page.getByTestId("step-preview")).toHaveCount(0);
  });

  test("cross-check warning shows when stud_diameter disagrees with (1A-1B)/2", async ({ page }) => {
    await page.goto("/parametric");
    await page.getByTestId("step1-next").click();
    await page.getByTestId("step2-next").click();
    await expect(page.getByTestId("step-measurements")).toBeVisible();

    // 1A=20, 1B=4 → expected stud=8, we type 16 → 8 mm drift → warning.
    // Input testids follow the schema key (with the `_mm` suffix).
    await page.getByTestId("measurement-input-outer_pitch_mm").fill("20");
    await page.getByTestId("measurement-input-inner_pitch_mm").fill("4");
    await page.getByTestId("measurement-input-stud_diameter_mm").fill("16");
    await expect(page.getByTestId("cross-check-warning")).toBeVisible();
    // Fix the stud number, warning disappears.
    await page.getByTestId("measurement-input-stud_diameter_mm").fill("8");
    await expect(page.getByTestId("cross-check-warning")).toHaveCount(0);
  });
});

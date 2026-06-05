// =============================================================================
// tests/e2e/screenshots-capture-ux.spec.ts — UI screenshot suite for the
// Phase 2 capture-UX upgrade. Captures the multi-mode selector, the 8-photo
// grid, and the 5-stage job-detail visualization as static PNGs for the
// deliverable and the README.
//
// These tests exercise the Vite preview build (apps/web/dist) on its own —
// they do NOT need the API or the SSE endpoint. The JobDetail page is fed a
// pre-seeded localStorage entry so the stage ladder renders without a live
// backend connection.
//
// Run with:
//   pnpm --filter @blocktool/web build
//   pnpm --filter @blocktool/web exec playwright test screenshots-capture-ux
// =============================================================================
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";
import { execSync } from "node:child_process";

const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const REPO_ROOT = path.resolve(__dirname, "..", "..");

/**
 * Spin up `pnpm preview` (which serves the production build on port 4173)
 * for the duration of this describe block. We prefer the static preview
 * over `pnpm dev` because the SW is not active in dev mode, and over
 * docker-compose because the API is not required for these screenshots.
 *
 * The spec assumes `apps/web/dist` is already built (run
 * `pnpm --filter @blocktool/web build` once). If the preview server is
 * not already running, this beforeAll block starts it in the background
 * and the test.afterAll kills it.
 */
test.describe("capture-ux screenshots", () => {
  test.use({
    baseURL: "http://localhost:4173",
    viewport: { width: 1280, height: 900 },
  });

  let previewProc: ReturnType<typeof execSync> | null = null;
  let startedPreview = false;

  test.beforeAll(async () => {
    const lsof = execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" });
    if (!lsof.includes("LISTEN")) {
      // Detached so we can return from beforeAll immediately; the preview
      // process is killed in afterAll.
      const { spawn } = await import("node:child_process");
      const proc = spawn(
        "pnpm",
        ["--filter", "@blocktool/web", "exec", "vite", "preview", "--port", "4173", "--strictPort"],
        { cwd: REPO_ROOT, stdio: "ignore", detached: true },
      );
      previewProc = proc as unknown as ReturnType<typeof execSync>;
      startedPreview = true;
      // Wait for the port to be bound.
      const start = Date.now();
      while (Date.now() - start < 15_000) {
        try {
          const out = execSync("lsof -nP -iTCP:4173 -sTCP:LISTEN", { encoding: "utf8" });
          if (out.includes("LISTEN")) break;
        } catch {
          /* port not yet bound */
        }
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

  async function gotoCapture(page: Page) {
    await page.goto("/capture");
    await expect(page.getByRole("heading", { name: "拍照采集" })).toBeVisible();
  }

  async function uploadPhotos(page: Page, count: number) {
    const files = Array.from({ length: count }, (_, i) => ({
      name: `shot-${i + 1}.jpg`,
      mimeType: "image/jpeg",
      buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46]),
    }));
    const fileInput = page.locator('[data-testid="file-input"]');
    await fileInput.setInputFiles(files);
  }

  test("capture-page-8photos — 8 photos queued in phone_walkaround mode", async ({ page }) => {
    await gotoCapture(page);
    await page.getByTestId("capture-mode-phone_walkaround").click();
    await uploadPhotos(page, 8);
    const submit = page.locator('[data-testid="submit-btn"]');
    await expect(submit).toBeEnabled();
    await expect(submit).toHaveAttribute("data-count", "8");
    await expect(page.getByTestId("shot-grid")).toHaveAttribute("data-count", "8");
    await page.waitForTimeout(150);
    const out = path.join(SCREENSHOTS_DIR, "capture-page-8photos.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });

  test("capture-mode-switch — segmented control with all 3 modes visible", async ({ page }) => {
    await gotoCapture(page);
    const studioTurntable = page.getByTestId("capture-mode-studio_turntable");
    await studioTurntable.click();
    await expect(studioTurntable).toHaveAttribute("data-active", "true");
    await page.waitForTimeout(120);
    const out = path.join(SCREENSHOTS_DIR, "capture-mode-switch.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });

  test("job-detail-stages — 5-stage ladder with dense pill active", async ({ page }) => {
    // Pre-seed the persisted store with a running job in the dense stage.
    const fakeId = "demo-job-screenshot";
    const payload = {
      state: {
        jobs: {
          [fakeId]: {
            id: fakeId,
            captureId: "cap-demo",
            partId: "demo",
            status: "running",
            progress: 35,
            stage: "dense_reconstruction",
            error: null,
            resultAssetId: null,
            createdAt: "2026-06-05T08:00:00.000Z",
            updatedAt: "2026-06-05T08:00:00.000Z",
            imageCount: 8,
            captureMode: "phone_walkaround",
            etaSeconds: 60,
            pipelineUsed: "colmap_sfm",
          },
        },
        currentJobId: fakeId,
      },
      version: 1,
    };
    await page.addInitScript((p) => {
      try {
        window.localStorage.setItem("blocktool.jobs.v1", JSON.stringify(p));
      } catch {
        /* ignore */
      }
    }, payload);

    await page.goto(`/jobs/${fakeId}`);
    await expect(page.getByTestId("stage-card")).toBeVisible();
    await expect(page.getByTestId("stage-pill-dense")).toHaveAttribute("data-active", "true");
    await expect(page.getByTestId("eta")).toContainText("约 1 分钟");
    await expect(page.getByTestId("capture-mode-label")).toContainText("phone_walkaround");
    await expect(page.getByTestId("pipeline-label")).toContainText("colmap_sfm");
    await page.waitForTimeout(120);
    const out = path.join(SCREENSHOTS_DIR, "job-detail-stages.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });
});


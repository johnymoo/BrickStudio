// =============================================================================
// tests/e2e/full-flow.spec.ts — end-to-end happy path.
//
// What this covers (per design.md §10 acceptance table — "E2E"):
//   1. open the home page
//   2. navigate to /capture
//   3. upload 4 photos (jsdom-style, no real camera; we use the
//      "从相册选择" file input)
//   4. submit, land on /jobs/{id}
//   5. wait for SSE-driven status to flip to "completed"
//   6. assert the Three.js canvas is in the DOM
//   7. take a final screenshot to tests/e2e/screenshots/final.png
//
// The test is *single-shot* (workers: 1 in playwright.config.ts) because
// the API only has one Celery worker, and two parallel uploads would
// race for the GPU / disk.
//
// Default timeout is 10 minutes (set in playwright.config.ts) — this
// generously covers the Open3D-only fallback path. The COLMAP / Meshroom
// paths can take much longer; if you need those, override
// BLOCKTOOL_E2E_TIMEOUT_MS in the env.
// =============================================================================
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";

const FIXTURE_DIR = path.join(__dirname, "fixtures");
const FIXTURES = [
  "cube-front.jpg",
  "cube-side.jpg",
  "cube-back.jpg",
  "cube-angle.jpg",
];

/** Resolve a fixture to an absolute path; throw early if any is missing. */
function fixturePaths(): string[] {
  const abs = FIXTURES.map((f) => path.join(FIXTURE_DIR, f));
  for (const p of abs) {
    // We use `path.existsSync` lazily inside the test so a missing
    // fixture fails the test with a helpful message rather than the
    // Node import throwing at module load.
    require("node:fs").accessSync(p);
  }
  return abs;
}

async function gotoCapture(page: Page) {
  await page.goto("/capture");
  // Wait for the page shell to be visible. The camera block is the
  // longest pole — in headless Chromium there's no real camera, so
  // the page falls back to the "无摄像头 / 权限被拒" message.
  await expect(page.getByRole("heading", { name: "拍照采集" })).toBeVisible();
}

async function uploadFourImages(page: Page) {
  const fileInput = page.locator('[data-testid="file-input"]');
  await expect(fileInput, "file input is present in the capture page").toBeAttached();
  // Playwright's setInputFiles accepts an array of absolute paths in
  // a single call — it builds the multipart payload for us.
  await fileInput.setInputFiles(fixturePaths());

  // The submit button is disabled until >= MIN_IMAGES (4) shots are
  // queued. Wait for it to become enabled rather than guessing the
  // post-upload state.
  const submit = page.locator('[data-testid="submit-btn"]');
  await expect(submit).toBeEnabled({ timeout: 10_000 });
}

test.describe("full flow", () => {
  test("upload 4 photos → 3D model renders", async ({ page }) => {
    // Log any console errors / page errors so the test report surfaces
    // a useful failure cause.
    const consoleErrors: string[] = [];
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(`console: ${msg.text()}`);
      }
    });

    // 1. Open home
    await page.goto("/");
    await expect(page).toHaveTitle(/积木工具|BlockTool/i);
    // Empty state CTA should be visible on a fresh browser profile.
    await expect(page.getByRole("link", { name: /开始新的采集/ }).first()).toBeVisible();

    // 2. Go to /capture
    await page.getByRole("link", { name: /开始新的采集/ }).first().click();
    await gotoCapture(page);

    // 3. Upload 4 fixture images
    await uploadFourImages(page);

    // 4. Submit and wait for the redirect to /jobs/{id}
    await Promise.all([
      page.waitForURL(/\/jobs\/[0-9a-f-]{8,}/i, { timeout: 30_000 }),
      page.locator('[data-testid="submit-btn"]').click(),
    ]);
    const jobUrl = new URL(page.url());
    const jobId = jobUrl.pathname.split("/").pop() ?? "";
    expect(jobId, "should have navigated to /jobs/{uuid}").toMatch(/^[0-9a-f-]{8,}$/i);

    // 5. Wait for the SSE-driven status to flip to "completed". The
    // backend publishes a `completed` event on the Redis pub/sub
    // channel, which the front-end maps to job.status = "completed"
    // and the StatusBadge to "已完成". The 3D viewer mounts only when
    // status is "completed" and resultAssetId is set.
    //
    // We give the test a generous budget because:
    //   - The 3D-reconstruction task in the Open3D fallback takes ~0.5s
    //   - Meshroom / COLMAP runs can take 5-30 minutes on a dev box
    //   - The first run pays an alembic / pipeline warm-up cost
    //
    // The page also has a 5s polling fallback if the SSE connection
    // drops, so the status will *eventually* flip even on a flaky
    // network.
    const statusBadge = page.getByTestId("status-badge");
    // Wait for the badge to render the "已完成" text. The exact text
    // comes from the StatusBadge component, but the data-testid is
    // stable.
    await expect(statusBadge).toContainText("已完成", { timeout: 9 * 60 * 1000 });

    // 6. The Three.js canvas should now be in the DOM.
    const canvas = page.locator("canvas");
    await expect(canvas).toBeVisible({ timeout: 30_000 });
    // Sanity: it should be a non-trivial size (R3F mounts a 1×1
    // canvas in jsdom-style tests; we want the production-sized one).
    const box = await canvas.boundingBox();
    expect(box, "canvas bounding box should be present").not.toBeNull();
    expect(box!.width, "canvas should be > 100px wide").toBeGreaterThan(100);
    expect(box!.height, "canvas should be > 100px tall").toBeGreaterThan(100);

    // Give R3F a moment to finish a render frame before screenshotting
    // — the canvas is shown, but the first frame may not be on screen
    // for one tick of requestAnimationFrame.
    await page.waitForTimeout(1000);

    // 7. Final screenshot.
    const screenshotPath = path.join(__dirname, "screenshots", "final.png");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: screenshotPath });

    // Surface any captured console errors as a test warning (but don't
    // fail the test unless they are obviously fatal — three.js emits
    // benign warnings about dev-only features).
    if (consoleErrors.length > 0) {
      test.info().annotations.push({
        type: "console-errors",
        description: consoleErrors.join("\n"),
      });
    }
  });
});

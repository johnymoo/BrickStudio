// =============================================================================
// tests/e2e/library.spec.ts — part library (issue #5) end-to-end.
// Mirrors parametric.spec.ts: mock /api/v1/library + /assets with page.route(),
// run against `vite preview` on 4173. No docker compose needed.
// Flow: open /library -> see a part -> open detail -> 标记已核验 -> badge flips.
// =============================================================================
import { test, expect, type Page } from "@playwright/test";
import path from "node:path";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";

const SCREENSHOTS_DIR = path.join(__dirname, "screenshots");
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const BASE_URL = process.env.BLOCKTOOL_E2E_MOCK_BASE_URL || "http://localhost:4173";
const PART_ID = "lib-e2e-001";
const ASSET_FIXTURE = path.join(__dirname, "fixtures", "real-bricks", "e2e-api-feile-2x2-brick.glb");
const ASSET_DATA_URL = `data:model/gltf-binary;base64,${readFileSync(ASSET_FIXTURE).toString("base64")}`;
const CREATED_AT = new Date(Date.UTC(2026, 5, 14, 10, 0, 0)).toISOString();
const VERIFIED_AT = new Date(Date.UTC(2026, 5, 14, 10, 0, 1)).toISOString();
const ASSET_EXPIRES_AT = new Date(Date.UTC(2026, 5, 15, 10, 0, 0)).toISOString();

const PART = {
  part_id: PART_ID,
  capture_id: "cap-e2e-001",
  asset_id: "asset-e2e-001",
  source_mode: "parametric_block",
  system: "feile",
  kind: "brick",
  units_x: 2,
  units_y: 2,
  derived_spec_mm: { unit_mm: 16.0 },
  color: null,
  name: "E2E FEILE 2x2",
  notes: null,
  status: "pending" as "pending" | "verified" | "rejected",
  created_at: CREATED_AT,
  updated_at: CREATED_AT,
};

async function mockLibraryApi(page: Page) {
  let current = { ...PART };

  await page.route("**/api/v1/library**", async (route, request) => {
    const url = new URL(request.url());
    const isDetailPath = url.pathname.endsWith(`/library/${PART_ID}`);
    const isListPath = url.pathname.endsWith("/library");

    if (isDetailPath && request.method() === "PATCH") {
      const patch = request.postDataJSON();
      expect(patch).toEqual({ status: "verified" });
      current = { ...current, ...patch, updated_at: VERIFIED_AT };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }

    if (isDetailPath && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(current),
      });
      return;
    }

    if (isListPath && request.method() === "GET") {
      const status = url.searchParams.get("status");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(!status || status === current.status ? [current] : []),
      });
      return;
    }

    await route.fulfill({
      status: isListPath || isDetailPath ? 405 : 404,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: isListPath || isDetailPath ? "METHOD_NOT_ALLOWED" : "NOT_FOUND",
          message: `Unexpected library request: ${request.method()} ${url.pathname}`,
        },
      }),
    });
  });

  await page.route(`**/api/v1/assets/${PART.asset_id}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        asset_id: PART.asset_id,
        kind: "mesh_gltf",
        url: ASSET_DATA_URL,
        expires_at: ASSET_EXPIRES_AT,
      }),
    });
  });
}

test.describe("part library", () => {
  test.use({
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 900 },
  });

  let previewProc: ReturnType<typeof execSync> | null = null;
  let startedPreview = false;

  test.beforeAll(async () => {
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

  test("header link navigates to /library and lists a part", async ({ page }) => {
    await mockLibraryApi(page);
    await page.goto("/");
    await expect(page.getByTestId("header-library-link")).toBeVisible();
    await page.getByTestId("header-library-link").click();
    await expect(page).toHaveURL(/\/library$/);
    await expect(page.getByText(PART.name)).toBeVisible();
  });

  test("open detail and mark verified flips the status badge", async ({ page }) => {
    await mockLibraryApi(page);
    await page.goto("/library");
    await page.getByText(PART.name).click();
    await expect(page).toHaveURL(new RegExp(`/library/${PART_ID}$`));
    await expect(page.getByTestId("part-status-badge")).toHaveAttribute("data-status", "pending");
    await page.getByTestId("part-verify-btn").click();
    await expect(page.getByTestId("part-status-badge")).toHaveAttribute("data-status", "verified");

    const out = path.join(SCREENSHOTS_DIR, "library-verified.png");
    await page.screenshot({ path: out, fullPage: true });
    test.info().annotations.push({ type: "screenshot", description: out });
  });
});

import { expect, test, request as pwRequest } from "@playwright/test";

const BASE_URL =
  process.env.BLOCKTOOL_E2E_BASE_URL ||
  process.env.PLAYWRIGHT_BASE_URL ||
  "http://localhost:80";

const ADMIN_TOKEN = process.env.LIBRARY_ADMIN_TOKEN || "blocktool_dev_admin_change_me";

const RAW = {
  outer_pitch_mm: 33.4,
  inner_pitch_mm: 6.6,
  stud_diameter_mm: 9.4,
  brick_height_net_mm: 19.2,
  brick_height_total_mm: 24.6,
};

test.describe("part library live integration", () => {
  test.use({ baseURL: BASE_URL, viewport: { width: 1280, height: 900 } });

  test("parametric capture promotes to library and can be verified", async ({ page, baseURL }) => {
    test.setTimeout(2 * 60 * 1000);
    const api = await pwRequest.newContext({ baseURL });

    try {
      const health = await api.get("/api/v1/health");
      expect(health.status(), "stack must be up").toBe(200);

      const partName = `live-library-${Date.now()}`;
      const created = await api.post("/api/v1/parametric-blocks", {
        multipart: {
          part_id: partName,
          system: "feile",
          kind: "brick",
          units_x: "2",
          units_y: "2",
          raw_measurements_mm: JSON.stringify(RAW),
        },
      });
      expect(created.status()).toBe(201);
      const createdBody = (await created.json()) as { job_id: string; capture_id: string };

      await expect
        .poll(
          async () => {
            const job = await api.get(`/api/v1/jobs/${createdBody.job_id}`);
            if (job.status() !== 200) return "missing";
            return ((await job.json()) as { status: string }).status;
          },
          { timeout: 60_000, intervals: [500, 1000, 2000] },
        )
        .toBe("completed");

      const library = await api.get("/api/v1/library?status=pending&limit=100");
      expect(library.status()).toBe(200);
      const parts = (await library.json()) as Array<{
        part_id: string;
        name: string;
        capture_id: string;
        status: string;
      }>;
      const part = parts.find((row) => row.name === partName);
      expect(part, "promoted part should be visible in pending library").toBeTruthy();
      expect(part?.capture_id).toBe(createdBody.capture_id);

      const patched = await api.patch(`/api/v1/library/${part!.part_id}`, {
        headers: { "X-Library-Admin-Token": ADMIN_TOKEN },
        data: { status: "verified" },
      });
      expect(patched.status()).toBe(200);
      expect(((await patched.json()) as { status: string }).status).toBe("verified");

      await page.goto("/library");
      await expect(page.getByRole("heading", { name: "零件库" })).toBeVisible();
    } finally {
      await api.dispose();
    }
  });
});

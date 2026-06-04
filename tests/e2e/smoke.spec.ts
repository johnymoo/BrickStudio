// =============================================================================
// tests/e2e/smoke.spec.ts — the most basic sanity checks for the stack.
//
// These should run *first* and finish in seconds. If smoke fails, the
// stack isn't up at all; don't waste time on the long full-flow test.
//
// We use raw `fetch` (and `request` from the Playwright fixture) instead
// of opening a real page, so the smoke test doesn't depend on the Web
// container being healthy — just on the API being reachable through the
// configured baseURL. The full-flow test exercises the Web container.
// =============================================================================
import { test, expect, request } from "@playwright/test";

test.describe("smoke", () => {
  test("GET /api/v1/health returns 200 with all subsystems ok", async ({ baseURL }) => {
    // Use a dedicated APIRequestContext so we hit the API directly even
    // if the proxy / web container is mis-configured. baseURL already
    // points at the proxy (or the API in dev), so a relative path is
    // enough.
    const ctx = await request.newContext({ baseURL });
    const res = await ctx.get("/api/v1/health");
    expect(res.status(), `unexpected status from /api/v1/health`).toBe(200);
    const body = await res.json();
    expect(body).toMatchObject({
      status: expect.stringMatching(/^(ok|degraded)$/),
      version: expect.any(String),
    });
    // The brief's "健康" row in the acceptance table: db + storage must
    // both be ok. (redis can be 'degraded' — it's not on the critical
    // path for a static /health hit.)
    expect(body.db).toBe("ok");
    expect(body.storage).toBe("ok");
  });

  test("GET / serves the PWA shell", async ({ page }) => {
    const res = await page.goto("/");
    expect(res, "GET / should return a response").not.toBeNull();
    expect(res!.status(), "GET / should be 2xx").toBeLessThan(400);
    // The PWA shell always renders the <div id="root"> mount point and
    // the title "积木工具" — together they prove the React app booted.
    await expect(page).toHaveTitle(/积木工具|BlockTool/i);
    await expect(page.locator("#root")).toBeAttached();
    // The header link to the capture page is always present (per the
    // production build's HTML). The home page also links to it from
    // the empty-state CTA.
    const captureLink = page.getByRole("link", { name: /开始新的采集/ }).first();
    await expect(captureLink).toBeVisible();
  });
});

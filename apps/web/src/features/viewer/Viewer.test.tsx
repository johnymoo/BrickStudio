import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { Viewer } from "@features/viewer/Viewer";

const FIXTURE_PATH = resolve(fileURLToPath(import.meta.url), "../../../../tests/fixtures/triangle.glb");
const FIXTURE_BYTES = readFileSync(FIXTURE_PATH);

function mockAssetEndpoint(url: string) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const u = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    if (!u.startsWith(url)) return new Response("not found", { status: 404 });
    // The asset endpoint is a JSON; the GLB itself is fetched from the resolved
    // URL (a separate fetch in drei's loader). Return both from the same mock.
    if (u.endsWith("/api/v1/assets/asset-1")) {
      return new Response(
        JSON.stringify({
          asset_id: "asset-1",
          kind: "mesh_gltf",
          url: "https://minio.local/test.glb",
          expires_at: "2026-06-05T10:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (u === "https://minio.local/test.glb") {
      return new Response(FIXTURE_BYTES, {
        status: 200,
        headers: { "Content-Type": "model/gltf-binary" },
      });
    }
    return new Response("not found", { status: 404 });
  });
}

describe("Viewer", () => {
  let originalFetch: typeof globalThis.fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    globalThis.fetch = mockAssetEndpoint("http") as typeof globalThis.fetch;
  });
  afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
  });

  it("renders the viewer container and the canvas element", async () => {
    render(<Viewer assetId="asset-1" />);
    const viewer = screen.getByTestId("viewer");
    expect(viewer).toBeInTheDocument();
    // Canvas (R3F) eventually mounts.
    await waitFor(() => {
      expect(viewer.querySelector("canvas")).toBeTruthy();
    });
  });

  it("exposes control buttons (reset, mode, bg swatches)", async () => {
    render(<Viewer assetId="asset-1" />);
    expect(screen.getByTestId("viewer-reset")).toBeInTheDocument();
    expect(screen.getByTestId("viewer-mode")).toBeInTheDocument();
    expect(screen.getByTestId("bg-#0f172a")).toBeInTheDocument();
    expect(screen.getByTestId("bg-#ffffff")).toBeInTheDocument();
  });

  it("shows an error banner when the asset endpoint fails", async () => {
    globalThis.fetch = vi.fn(async () => new Response("server down", { status: 500 })) as typeof globalThis.fetch;
    render(<Viewer assetId="asset-bad" />);
    await waitFor(() => expect(screen.getByTestId("viewer-error")).toBeInTheDocument());
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, cleanup, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { useGLTF } from "@react-three/drei";
import { Viewer } from "@features/viewer/Viewer";

vi.mock("@react-three/fiber", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const threeTags = new Set(["color", "ambientLight", "directionalLight"]);
  const renderNode = (node: ReactNode): ReactNode => {
    if (Array.isArray(node)) {
      return node.map((child, index) =>
        React.createElement(React.Fragment, { key: index }, renderNode(child)),
      );
    }
    if (!React.isValidElement<{ children?: ReactNode }>(node)) return node;
    if (typeof node.type === "string" && threeTags.has(node.type)) return null;
    return React.cloneElement(node, undefined, renderNode(node.props.children));
  };

  return {
    Canvas: ({ children, ...props }: { children?: ReactNode; [key: string]: unknown }) =>
      React.createElement(
        "div",
        { "data-testid": props["data-testid"] },
        React.createElement("canvas"),
        renderNode(children),
      ),
    useThree: () => ({
      camera: {
        fov: 50,
        position: { set: () => undefined },
        lookAt: () => undefined,
        updateProjectionMatrix: () => undefined,
      },
    }),
  };
});

vi.mock("@react-three/drei", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const never = new Promise(() => undefined);
  const PassThrough = ({ children }: { children?: ReactNode }) => React.createElement(React.Fragment, null, children);

  return {
    Bounds: PassThrough,
    Center: PassThrough,
    Html: PassThrough,
    OrbitControls: () => null,
    useGLTF: vi.fn(() => {
      throw never;
    }),
  };
});

const FIXTURE_PATH = resolve(fileURLToPath(import.meta.url), "../../../../tests/fixtures/triangle.glb");
const FIXTURE_BYTES = readFileSync(FIXTURE_PATH);
const useGLTFMock = vi.mocked(useGLTF);

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
          url: "<PRIVATE_URL>",
          expires_at: "<PRIVATE_DATE>",
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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("Viewer", () => {
  let originalFetch: typeof globalThis.fetch;
  beforeEach(() => {
    originalFetch = globalThis.fetch;
    useGLTFMock.mockClear();
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
    expect(screen.getByTestId("bg-var(--camera-bg)")).toBeInTheDocument();
    expect(screen.getByTestId("bg-#ffffff")).toBeInTheDocument();
  });

  it("shows an error banner when the asset endpoint fails", async () => {
    globalThis.fetch = vi.fn(async () => new Response("server down", { status: 500 })) as typeof globalThis.fetch;
    render(<Viewer assetId="asset-bad" />);
    await waitFor(() => expect(screen.getByTestId("viewer-error")).toBeInTheDocument());
  });

  it("aborts stale asset URL requests and ignores their resolved URLs", async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const assetSignals: Record<string, AbortSignal | undefined> = {};

    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const u = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (u.endsWith("/api/v1/assets/asset-1")) {
        assetSignals["asset-1"] = init?.signal ?? undefined;
        return first.promise;
      }
      if (u.endsWith("/api/v1/assets/asset-2")) {
        assetSignals["asset-2"] = init?.signal ?? undefined;
        return second.promise;
      }
      return Promise.resolve(
        new Response(FIXTURE_BYTES, {
          status: 200,
          headers: { "Content-Type": "model/gltf-binary" },
        }),
      );
    }) as typeof globalThis.fetch;

    const { rerender } = render(<Viewer assetId="asset-1" />);
    await waitFor(() => expect(assetSignals["asset-1"]).toBeInstanceOf(AbortSignal));

    rerender(<Viewer assetId="asset-2" />);
    await waitFor(() => expect(assetSignals["asset-2"]).toBeInstanceOf(AbortSignal));
    expect(assetSignals["asset-1"]?.aborted).toBe(true);

    await act(async () => {
      first.resolve(
        new Response(
          JSON.stringify({
            asset_id: "asset-1",
            kind: "mesh_gltf",
            url: "<PRIVATE_URL_STALE>",
            expires_at: "<PRIVATE_DATE>",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
      second.resolve(
        new Response(
          JSON.stringify({
            asset_id: "asset-2",
            kind: "mesh_gltf",
            url: "<PRIVATE_URL_CURRENT>",
            expires_at: "<PRIVATE_DATE>",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
      await second.promise;
    });

    await waitFor(() => expect(useGLTFMock).toHaveBeenCalledWith("<PRIVATE_URL_CURRENT>"));
    expect(useGLTFMock).not.toHaveBeenCalledWith("<PRIVATE_URL_STALE>");
    expect(screen.queryByTestId("viewer-error")).not.toBeInTheDocument();
    expect(assetSignals["asset-2"]?.aborted).toBe(false);
  });
});

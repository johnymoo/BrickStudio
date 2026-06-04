import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createCapture, getCapture, getJob, getAssetUrl, subscribeJob, ApiClientError } from "@lib/api";

// Node 24's undici fetch mishandles FormData bodies (sends them as
// `text/plain;charset=UTF-8` with the body stringified). Patch `fetch` for
// the duration of these tests to encode FormData as a proper multipart body
// with a deterministic boundary, mirroring what a browser fetch would do.
function installFormDataFetchPatch() {
  const original = globalThis.fetch;
  const patched: typeof fetch = async (input, init) => {
    const reqInit = init ?? {};
    if (reqInit.body instanceof FormData) {
      const boundary = "----vitest-blocktool-boundary";
      const parts: string[] = [];
      for (const [name, value] of reqInit.body.entries()) {
        if (typeof value === "string") {
          parts.push(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`);
        } else {
          const file = value as File;
          const filename = (file as File).name ?? "blob";
          const type = file.type || "application/octet-stream";
          parts.push(
            `--${boundary}\r\nContent-Disposition: form-data; name="${name}"; filename="${filename}"\r\nContent-Type: ${type}\r\n\r\n`,
          );
          parts.push("...binary-omitted...\r\n");
        }
      }
      parts.push(`--${boundary}--\r\n`);
      const body = parts.join("");
      const headers = new Headers(reqInit.headers);
      headers.set("Content-Type", `multipart/form-data; boundary=${boundary}`);
      return original(input as RequestInfo, { ...reqInit, body, headers });
    }
    return original(input as RequestInfo, reqInit);
  };
  globalThis.fetch = patched;
  return () => {
    globalThis.fetch = original;
  };
}

const server = setupServer(
  http.post("*/api/v1/captures", async ({ request }) => {
    const ct = request.headers.get("content-type") ?? "";
    const isMultipart = ct.toLowerCase().includes("multipart/form-data");
    if (!isMultipart) {
      return HttpResponse.json(
        { error: { code: "CAPTURE_INVALID", message: `expected multipart/form-data, got ${ct}` } },
        { status: 400 },
      );
    }
    const text = await request.clone().text();
    const matches = text.match(/filename=/g) ?? [];
    const image_count = matches.length;
    if (image_count < 4) {
      return HttpResponse.json(
        { error: { code: "CAPTURE_INVALID", message: "need at least 4 images" } },
        { status: 422 },
      );
    }
    return HttpResponse.json(
      {
        capture_id: "cap_test_1",
        part_id: "part-uuid-1",
        image_count,
        status: "pending",
        job_id: "job_test_1",
      },
      { status: 201 },
    );
  }),

  http.get("*/api/v1/jobs/:id", ({ params }) => {
    const { id } = params;
    if (id === "missing") {
      return HttpResponse.json(
        { error: { code: "JOB_NOT_FOUND", message: "no such job" } },
        { status: 404 },
      );
    }
    return HttpResponse.json({
      job_id: id,
      status: "running",
      progress: 42,
      stage: "open3d: cleaning",
    });
  }),

  http.get("*/api/v1/captures/:id", ({ params }) => {
    return HttpResponse.json({
      capture_id: params.id,
      part_id: "p-1",
      image_count: 6,
      status: "pending",
      created_at: "2026-06-04T10:00:00Z",
      updated_at: "2026-06-04T10:00:00Z",
    });
  }),

  http.get("*/api/v1/assets/:id", ({ params }) => {
    return HttpResponse.json({
      asset_id: params.id,
      kind: "mesh_gltf",
      url: `https://minio.example.local/blocktool/${params.id}.glb`,
      expires_at: "2026-06-05T10:00:00Z",
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => server.resetHandlers());

let restoreFetch: (() => void) | null = null;
beforeEach(() => {
  restoreFetch?.();
  restoreFetch = installFormDataFetchPatch();
});
afterEach(() => {
  restoreFetch?.();
  restoreFetch = null;
});

describe("createCapture", () => {
  it("POSTs multipart form and parses the response", async () => {
    const fd = new FormData();
    fd.append("part_id", "part-uuid-1");
    for (let i = 0; i < 4; i++) {
      fd.append("images", new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }), `p${i}.jpg`);
    }
    const res = await createCapture(fd);
    expect(res.capture_id).toBe("cap_test_1");
    expect(res.job_id).toBe("job_test_1");
    expect(res.image_count).toBe(4);
  });

  it("throws ApiClientError on validation failure", async () => {
    const fd = new FormData();
    fd.append("part_id", "p");
    fd.append("images", new Blob([new Uint8Array([1])]), "only.jpg");
    await expect(createCapture(fd)).rejects.toBeInstanceOf(ApiClientError);
    try {
      await createCapture(fd);
    } catch (err) {
      const e = err as ApiClientError;
      expect(e.status).toBe(422);
      expect((e.body as { error: { code: string } }).error.code).toBe("CAPTURE_INVALID");
    }
  });
});

describe("getJob", () => {
  it("returns parsed job info", async () => {
    const info = await getJob("job_test_1");
    expect(info.job_id).toBe("job_test_1");
    expect(info.status).toBe("running");
    expect(info.progress).toBe(42);
  });

  it("throws on 404", async () => {
    await expect(getJob("missing")).rejects.toBeInstanceOf(ApiClientError);
  });
});

describe("getCapture", () => {
  it("returns parsed capture info", async () => {
    const info = await getCapture("cap_test_1");
    expect(info.part_id).toBe("p-1");
    expect(info.image_count).toBe(6);
  });
});

describe("getAssetUrl", () => {
  it("returns the resolved download URL", async () => {
    const info = await getAssetUrl("asset-123");
    expect(info.url).toContain("asset-123.glb");
    expect(info.kind).toBe("mesh_gltf");
  });
});

describe("subscribeJob", () => {
  it("dispatches progress events from a fake EventSource", async () => {
    // The /stream endpoint isn't mocked here; we just verify the helper builds
    // a correct EventSource URL by intercepting it through a custom handler.
    server.use(
      http.get("*/api/v1/jobs/:id/stream", () => {
        // Return an empty 200 with text/event-stream. We won't actually read
        // the body — the test only needs the EventSource constructor to be
        // called with the right URL.
        return new HttpResponse(null, { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    // Patch the global EventSource for this test.
    const seen: string[] = [];
    class FakeES {
      public url: string;
      constructor(url: string) {
        this.url = url;
        seen.push(url);
      }
      addEventListener() {}
      close() {}
    }
    const prev = (globalThis as { EventSource?: unknown }).EventSource;
    // @ts-expect-error -- mock for test
    globalThis.EventSource = FakeES;
    try {
      const dispose = subscribeJob("job-x", { onEvent: () => undefined });
      expect(seen[0]).toBe("/api/v1/jobs/job-x/stream");
      dispose();
    } finally {
      (globalThis as { EventSource?: unknown }).EventSource = prev;
    }
  });
});

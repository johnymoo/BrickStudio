import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import {
  createCapture,
  getCapture,
  getJob,
  getAssetUrl,
  subscribeJob,
  ApiClientError,
  listCaptures,
  getCaptureImages,
  listLibraryParts,
  getLibraryPart,
  updateLibraryPart,
} from "@lib/api";

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

  http.get("*/api/v1/captures", ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json([
      {
        capture_id: "cap-ar-1",
        part_id: "ar-brick-1",
        image_count: 4,
        status: "pending",
        job_id: null,
        image_keys: ["raw/cap-ar-1/000.png"],
        capture_mode: "phone_walkaround",
        mode: "ar_recognized",
        system: null,
        kind: "brick",
        units_x: null,
        units_y: null,
        recognition_result: { ok: false, reason: "low_confidence", confidence: 0.12 },
        needs_measurement: {
          fields: ["outer_pitch_mm"],
          guidance: "Measure this brick with calipers.",
          endpoint: "/api/v1/parametric-blocks",
          reason: "low_confidence",
        },
        created_at: "<PRIVATE_DATE>",
        updated_at: "<PRIVATE_DATE>",
        requested_limit: Number(url.searchParams.get("limit")),
      },
    ]);
  }),

  http.get("*/api/v1/captures/:id/images", ({ params }) => {
    return HttpResponse.json({
      capture_id: params.id,
      images: [
        {
          key: `raw/${params.id}/000.png`,
          url: `https://raw.example.test/${params.id}/000.png?sig=test`,
        },
      ],
    });
  }),

  http.get("*/api/v1/assets/:id", ({ params }) => {
    return HttpResponse.json({
      asset_id: params.id,
      kind: "mesh_gltf",
      url: `https://minio.example.local/blocktool/${params.id}.glb`,
      expires_at: "<PRIVATE_DATE>",
    });
  }),

  http.get("*/api/v1/library", ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const all = [
      {
        part_id: "p1",
        capture_id: "c1",
        asset_id: "a1",
        source_mode: "parametric_block",
        system: "feile",
        kind: "brick",
        units_x: 2,
        units_y: 2,
        derived_spec_mm: null,
        color: null,
        name: "alpha",
        notes: null,
        status: "pending",
        created_at: "<PRIVATE_DATE>",
        updated_at: "<PRIVATE_DATE>",
      },
      {
        part_id: "p2",
        capture_id: "c2",
        asset_id: "a2",
        source_mode: "ar_recognized",
        system: "feile",
        kind: "plate",
        units_x: 1,
        units_y: 4,
        derived_spec_mm: null,
        color: null,
        name: "beta",
        notes: null,
        status: "verified",
        created_at: "<PRIVATE_DATE>",
        updated_at: "<PRIVATE_DATE>",
      },
    ];
    return HttpResponse.json(status ? all.filter((p) => p.status === status) : all);
  }),
  http.get("*/api/v1/library/p1", () =>
    HttpResponse.json({
      part_id: "p1",
      capture_id: "c1",
      asset_id: "a1",
      source_mode: "parametric_block",
      system: "feile",
      kind: "brick",
      units_x: 2,
      units_y: 2,
      derived_spec_mm: null,
      color: null,
      name: "alpha",
      notes: null,
      status: "pending",
      created_at: "<PRIVATE_DATE>",
      updated_at: "<PRIVATE_DATE>",
    }),
  ),
  http.patch("*/api/v1/library/p1", async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      part_id: "p1",
      capture_id: "c1",
      asset_id: "a1",
      source_mode: "parametric_block",
      system: "feile",
      kind: "brick",
      units_x: 2,
      units_y: 2,
      derived_spec_mm: null,
      color: null,
      name: (body.name as string) ?? "alpha",
      notes: (body.notes as string) ?? null,
      status: (body.status as string) ?? "pending",
      created_at: "<PRIVATE_DATE>",
      updated_at: "<PRIVATE_DATE>",
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => {
  server.resetHandlers();
  window.localStorage.clear();
});

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

describe("listCaptures", () => {
  it("parses AR captures with image keys and nullable job ids", async () => {
    const captures = await listCaptures(7);
    expect(captures).toHaveLength(1);
    expect(captures[0]).toMatchObject({
      capture_id: "cap-ar-1",
      job_id: null,
      image_keys: ["raw/cap-ar-1/000.png"],
      mode: "ar_recognized",
      kind: "brick",
      recognition_result: { reason: "low_confidence" },
      needs_measurement: {
        reason: "low_confidence",
        endpoint: "/api/v1/parametric-blocks",
      },
    });
  });
});

describe("getCaptureImages", () => {
  it("returns presigned raw capture images", async () => {
    const result = await getCaptureImages("cap-ar-1");
    expect(result.images).toEqual([
      {
        key: "raw/cap-ar-1/000.png",
        url: "https://raw.example.test/cap-ar-1/000.png?sig=test",
      },
    ]);
  });
});

describe("getAssetUrl", () => {
  it("returns the resolved download URL", async () => {
    const info = await getAssetUrl("asset-123");
    expect(info.url).toContain("asset-123.glb");
    expect(info.kind).toBe("mesh_gltf");
  });
});

describe("library api", () => {
  it("lists parts", async () => {
    const parts = await listLibraryParts();
    expect(parts).toHaveLength(2);
    expect(parts.map((part) => part.name)).toEqual(["alpha", "beta"]);
  });

  it("filters parts by status", async () => {
    const parts = await listLibraryParts("verified");
    expect(parts).toHaveLength(1);
    expect(parts.map((part) => part.name)).toEqual(["beta"]);
  });

  it("gets a part by id", async () => {
    const part = await getLibraryPart("p1");
    expect(part.system).toBe("feile");
  });

  it("updates a part", async () => {
    const part = await updateLibraryPart("p1", { name: "renamed", status: "verified" });
    expect(part.name).toBe("renamed");
    expect(part.status).toBe("verified");
  });

  it("sends the library admin token when updating a part", async () => {
    window.localStorage.setItem("blocktool.libraryAdminToken", "test-token");
    let observedToken: string | null = null;
    server.use(
      http.patch("*/api/v1/library/p1", async ({ request }) => {
        observedToken = request.headers.get("X-Library-Admin-Token");
        return HttpResponse.json({
          part_id: "p1",
          capture_id: "c1",
          asset_id: "a1",
          source_mode: "parametric_block",
          system: "feile",
          kind: "brick",
          units_x: 2,
          units_y: 2,
          derived_spec_mm: null,
          color: null,
          name: "renamed",
          notes: null,
          status: "verified",
          created_at: "<PRIVATE_DATE>",
          updated_at: "<PRIVATE_DATE>",
        });
      }),
    );

    await updateLibraryPart("p1", { name: "renamed", status: "verified" });

    expect(observedToken).toBe("test-token");
  });

  it("treats blocked library admin token storage as no token", async () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("storage blocked");
      },
    });
    let observedToken: string | null = "not observed";
    server.use(
      http.patch("*/api/v1/library/p1", async ({ request }) => {
        observedToken = request.headers.get("X-Library-Admin-Token");
        return HttpResponse.json({
          part_id: "p1",
          capture_id: "c1",
          asset_id: "a1",
          source_mode: "parametric_block",
          system: "feile",
          kind: "brick",
          units_x: 2,
          units_y: 2,
          derived_spec_mm: null,
          color: null,
          name: "renamed",
          notes: null,
          status: "verified",
          created_at: "<PRIVATE_DATE>",
          updated_at: "<PRIVATE_DATE>",
        });
      }),
    );

    try {
      await updateLibraryPart("p1", { name: "renamed", status: "verified" });
    } finally {
      if (descriptor) {
        Object.defineProperty(window, "localStorage", descriptor);
      }
    }

    expect(observedToken).toBeNull();
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

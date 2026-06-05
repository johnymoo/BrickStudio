import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { JobDetail } from "@features/jobs/JobDetail";
import { useJobStore } from "@stores/useJobStore";
import { installEventSourceMock } from "../../../tests/helpers";

// Mock the API module so we don't actually hit the network.
vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    getJob: vi.fn(),
  };
});

import { getJob } from "@lib/api";
const getJobMock = vi.mocked(getJob);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

function seedJob(id: string, status: "pending" | "running" | "completed" | "failed" = "running", stage: string | null = "downloading_images", progress = 5) {
  useJobStore.setState((state) => ({
    jobs: {
      ...state.jobs,
      [id]: {
        id,
        captureId: `cap-${id}`,
        partId: id,
        status,
        progress,
        stage,
        error: null,
        resultAssetId: status === "completed" ? `asset-${id}` : null,
        createdAt: "2026-06-04T10:00:00.000Z",
        updatedAt: "2026-06-04T10:00:00.000Z",
        imageCount: 8,
        captureMode: "phone_walkaround",
        etaSeconds: null,
        pipelineUsed: null,
      },
    },
    currentJobId: id,
  }));
}

describe("JobDetail", () => {
  beforeEach(() => {
    useJobStore.setState({ jobs: {}, currentJobId: null });
    getJobMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    // Remove any EventSource mock left from the previous test.
    delete (globalThis as { EventSource?: unknown }).EventSource;
  });

  it("renders the 5-stage ladder with the correct active stage pill", async () => {
    installEventSourceMock();
    seedJob("j-1", "running", "downloading_images", 3);
    renderAt("/jobs/j-1");

    // Ladder present
    const ladder = await screen.findByTestId("stage-ladder");
    expect(ladder).toBeInTheDocument();
    // All 5 pills
    expect(screen.getByTestId("stage-pill-download")).toBeInTheDocument();
    expect(screen.getByTestId("stage-pill-sparse")).toBeInTheDocument();
    expect(screen.getByTestId("stage-pill-dense")).toBeInTheDocument();
    expect(screen.getByTestId("stage-pill-mesh")).toBeInTheDocument();
    expect(screen.getByTestId("stage-pill-done")).toBeInTheDocument();

    // The download pill is the current stage.
    expect(screen.getByTestId("stage-pill-download").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("stage-card").getAttribute("data-stage")).toBe("download");
  });

  it("updates the active stage as SSE progress events arrive", async () => {
    const es = installEventSourceMock();
    seedJob("j-2", "running", "downloading_images", 2);
    renderAt("/jobs/j-2");
    const stream = es.instances[0]!;
    expect(stream).toBeDefined();

    // Stage 1: sparse
    await act(async () => {
      stream.__dispatch("progress", { progress: 12, stage: "sparse_reconstruction" });
    });
    await waitFor(() => expect(screen.getByTestId("stage-pill-sparse").getAttribute("data-active")).toBe("true"));
    expect(screen.getByTestId("stage-pill-sparse").getAttribute("data-reached")).toBe("true");

    // Stage 2: dense
    await act(async () => {
      stream.__dispatch("stage_change", { stage: "dense_reconstruction" });
    });
    await waitFor(() => expect(screen.getByTestId("stage-pill-dense").getAttribute("data-active")).toBe("true"));

    // Stage 3: mesh
    await act(async () => {
      stream.__dispatch("stage_change", { stage: "mesh_reconstruction" });
    });
    await waitFor(() => expect(screen.getByTestId("stage-pill-mesh").getAttribute("data-active")).toBe("true"));

    // Completed
    await act(async () => {
      stream.__dispatch("completed", { result_asset_id: "asset-j-2" });
    });
    await waitFor(() => {
      expect(useJobStore.getState().jobs["j-2"]?.status).toBe("completed");
    });
    expect(screen.getByTestId("stage-pill-done").getAttribute("data-active")).toBe("true");
  });

  it("displays ETA in seconds when the SSE event includes eta_seconds", async () => {
    const es = installEventSourceMock();
    seedJob("j-3", "running", "sparse_reconstruction", 10);
    renderAt("/jobs/j-3");
    const stream = es.instances[0]!;

    await act(async () => {
      stream.__dispatch("progress", { progress: 25, stage: "sparse_reconstruction", eta_seconds: 42 });
    });
    await waitFor(() => {
      const eta = screen.queryByTestId("eta");
      expect(eta?.textContent).toMatch(/约 42 秒/);
    });

    await act(async () => {
      stream.__dispatch("progress", { progress: 40, stage: "dense_reconstruction", eta_seconds: 180 });
    });
    await waitFor(() => expect(screen.getByTestId("eta").textContent).toMatch(/约 3 分钟/));
  });

  it("flashes the stage card on stage transitions", async () => {
    const es = installEventSourceMock();
    seedJob("j-4", "running", "downloading_images", 1);
    renderAt("/jobs/j-4");
    const stream = es.instances[0]!;

    expect(screen.getByTestId("stage-card").getAttribute("data-highlight")).toBe("false");
    await act(async () => {
      stream.__dispatch("stage_change", { stage: "sparse_reconstruction" });
    });
    expect(screen.getByTestId("stage-card").getAttribute("data-highlight")).toBe("true");
    // The same stage again doesn't re-trigger the highlight.
    await act(async () => {
      stream.__dispatch("progress", { progress: 14, stage: "sparse_reconstruction" });
    });
    // Still highlighted (we're within the 1.2s window).
    expect(screen.getByTestId("stage-card").getAttribute("data-highlight")).toBe("true");
  });

  it("marks the job as failed and switches the card to the failed stage", async () => {
    const es = installEventSourceMock();
    seedJob("j-5", "running", "sparse_reconstruction", 12);
    renderAt("/jobs/j-5");
    const stream = es.instances[0]!;

    await act(async () => {
      stream.__dispatch("failed", { error: "COLMAP crashed" });
    });
    await waitFor(() => expect(useJobStore.getState().jobs["j-5"]?.status).toBe("failed"));
    expect(screen.getByTestId("failed-card")).toBeInTheDocument();
  });

  it("falls back to a 404 error state when the job id is unknown to the backend", async () => {
    installEventSourceMock();
    getJobMock.mockRejectedValue(Object.assign(new Error("not found"), { status: 404 }));
    renderAt("/jobs/missing");
    await waitFor(() => expect(screen.getByText("任务不存在")).toBeInTheDocument());
  });
});

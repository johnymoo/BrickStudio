import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ParametricPage } from "@features/parametric/ParametricPage";
import { installEventSourceMock } from "../../../tests/helpers";

// Mock @lib/api so we can capture submit() calls without hitting the network.
vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    createCapture: vi.fn(),
    getCapture: vi.fn(),
    getJob: vi.fn(),
    subscribeJob: vi.fn(() => () => undefined),
  };
});

vi.mock("@features/parametric/api", async () => {
  const actual = await vi.importActual("@features/parametric/api");
  return {
    ...actual,
    createParametricBlock: vi.fn(),
  };
});

import { createParametricBlock } from "@features/parametric/api";
import { subscribeJob, type JobStreamEvent } from "@lib/api";
import { AppHeader } from "@components/AppHeader";
import { useJobStore } from "@stores/useJobStore";
const createParametricBlockMock = vi.mocked(createParametricBlock);
const subscribeJobMock = vi.mocked(subscribeJob);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/parametric"]}>
      <ParametricPage />
    </MemoryRouter>,
  );
}

function makeJpegFile(name: string): File {
  return new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], name, { type: "image/jpeg" });
}

describe("ParametricPage", () => {
  beforeEach(() => {
    createParametricBlockMock.mockReset();
    subscribeJobMock.mockReset();
    subscribeJobMock.mockReturnValue(() => undefined);
    useJobStore.setState({ jobs: {}, currentJobId: null });
    localStorage.clear();
    // Provide a SSE mock so PreviewStep doesn't crash on first mount.
    installEventSourceMock();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    delete (globalThis as { EventSource?: unknown }).EventSource;
  });

  async function submitCompleteParametricBlock() {
    renderPage();

    await act(async () => {
      fireEvent.change(screen.getByTestId("photo-input"), {
        target: { files: [makeJpegFile("a.jpg"), makeJpegFile("b.jpg")] },
      });
    });
    await waitFor(() => expect(screen.getByTestId("photo-grid")).toHaveAttribute("data-count", "2"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("step1-next"));
    });

    await waitFor(() => expect(screen.getByTestId("step-kind")).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByTestId("system-lego"));
      fireEvent.click(screen.getByTestId("kind-tile"));
      fireEvent.click(screen.getByTestId("units-x-inc"));
      fireEvent.click(screen.getByTestId("units-x-inc"));
      fireEvent.click(screen.getByTestId("step2-next"));
    });

    await waitFor(() => expect(screen.getByTestId("step-measurements")).toBeInTheDocument());
    for (const [k, v] of Object.entries({
      outer_pitch_mm: 16.0,
      inner_pitch_mm: 7.2,
      stud_diameter_mm: 4.4,
      brick_height_net_mm: 9.5,
      brick_height_total_mm: 11.2,
    })) {
      await act(async () => {
        fireEvent.change(screen.getByTestId(`measurement-input-${k}`), { target: { value: String(v) } });
      });
    }
    await waitFor(() => expect(screen.getByTestId("step3-next")).not.toBeDisabled());
    await act(async () => {
      fireEvent.click(screen.getByTestId("step3-next"));
    });

    await waitFor(() => expect(screen.getByTestId("step-preview")).toBeInTheDocument());
  }

  it("starts on step 1 (photos) with the stepper visible", () => {
    renderPage();
    expect(screen.getByTestId("wizard-stepper")).toBeInTheDocument();
    // The active step pill lives on the button inside the stepper-1 li.
    const step1Li = screen.getByTestId("stepper-1");
    const step1Btn = step1Li.querySelector("button")!;
    expect(step1Btn).toHaveAttribute("data-active", "true");
    expect(step1Btn).toHaveAttribute("aria-current", "step");
    expect(screen.getByTestId("step-photos")).toBeInTheDocument();
  });

  it("advances through the 4 steps and submits the 5 caliper numbers", async () => {
    createParametricBlockMock.mockResolvedValue({
      capture_id: "cap-1",
      job_id: "job-1",
      part_id: "p-1",
      status: "completed",
      mode: "parametric_block",
      system: "duplo",
      kind: "brick",
      units_x: 2,
      units_y: 2,
      raw_measurements_mm: {
        outer_pitch_mm: 35.95,
        inner_pitch_mm: 4.05,
        stud_diameter_mm: 16.1,
        brick_height_net_mm: 17.05,
        brick_height_total_mm: 24.1,
      },
      derived_spec_mm: { unit_mm: 20, height_mm: 17, knob_diameter_mm: 16, knob_height_mm: 7.05 },
      cross_check_warnings: [],
      created_at: "2026-06-06T10:00:00Z",
    });

    renderPage();

    // ---- step 1: photos (skip) ----
    const step1Next = await screen.findByTestId("step1-next");
    await act(async () => {
      fireEvent.click(step1Next);
    });

    // ---- step 2: kind ----
    await waitFor(() => expect(screen.getByTestId("step-kind")).toBeInTheDocument());
    expect(screen.getByTestId("system-duplo")).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("kind-brick")).toHaveAttribute("data-active", "true");
    // switch to lego + tile to make sure the test covers non-defaults
    await act(async () => {
      fireEvent.click(screen.getByTestId("system-lego"));
    });
    await waitFor(() => expect(screen.getByTestId("system-lego")).toHaveAttribute("data-active", "true"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("kind-tile"));
    });
    // units_x 1 → 3 via the + button
    const unitsXInc = screen.getByTestId("units-x-inc");
    await act(async () => {
      fireEvent.click(unitsXInc);
      fireEvent.click(unitsXInc);
    });
    expect((screen.getByTestId("units-x-input") as HTMLInputElement).value).toBe("3");
    expect(screen.getByTestId("form-units-x").textContent).toBe("3");
    expect(screen.getByTestId("form-system").textContent).toBe("lego");
    expect(screen.getByTestId("form-kind").textContent).toBe("tile");
    const step2Next = screen.getByTestId("step2-next");
    await act(async () => {
      fireEvent.click(step2Next);
    });

    // ---- step 3: measurements ----
    await waitFor(() => expect(screen.getByTestId("step-measurements")).toBeInTheDocument());
    const next3 = screen.getByTestId("step3-next");
    expect(next3).toBeDisabled();

    // The 5 keys MUST use the `_mm` suffix to match the backend contract
    // (apps/api/src/api/v1/parametric_blocks.py:_RAW_KEYS).
    const measurements = {
      outer_pitch_mm: 16.0,
      inner_pitch_mm: 7.2,
      stud_diameter_mm: 4.4,
      brick_height_net_mm: 9.5,
      brick_height_total_mm: 11.2,
    };
    for (const [k, v] of Object.entries(measurements)) {
      const input = screen.getByTestId(`measurement-input-${k}`) as HTMLInputElement;
      await act(async () => {
        fireEvent.change(input, { target: { value: String(v) } });
      });
    }
    // Verify all 5 fields are flagged valid.
    for (const k of Object.keys(measurements)) {
      expect(screen.getByTestId(`measurement-${k}`)).toHaveAttribute("data-valid", "true");
    }
    await waitFor(() => expect(screen.getByTestId("step3-next")).not.toBeDisabled());
    await act(async () => {
      fireEvent.click(screen.getByTestId("step3-next"));
    });

    // ---- step 4: preview ----
    await waitFor(() => expect(screen.getByTestId("step-preview")).toBeInTheDocument());
    await waitFor(() => expect(createParametricBlockMock).toHaveBeenCalledTimes(1));

    // Verify the API was called with the right shape. We check the
    // raw_measurements_mm keys explicitly — the contract is that every
    // key carries the `_mm` suffix the backend enforces.
    const arg = createParametricBlockMock.mock.calls[0]![0];
    expect(arg.system).toBe("lego");
    expect(arg.kind).toBe("tile");
    expect(arg.units_x).toBe(3);
    expect(arg.units_y).toBe(2);
    expect(arg.raw_measurements_mm).toEqual(measurements);
    expect(Object.keys(arg.raw_measurements_mm).sort()).toEqual(
      ["brick_height_net_mm", "brick_height_total_mm", "inner_pitch_mm", "outer_pitch_mm", "stud_diameter_mm"].sort(),
    );
    expect(arg.photos).toBeUndefined(); // no photos in this test
  });

  it("persists a parametric job in the local job store after successful submit", async () => {
    createParametricBlockMock.mockResolvedValue({
      capture_id: "cap-1",
      job_id: "job-1",
      part_id: "p-1",
      status: "pending",
      mode: "parametric_block",
      system: "lego",
      kind: "tile",
      units_x: 3,
      units_y: 2,
      raw_measurements_mm: {
        outer_pitch_mm: 16,
        inner_pitch_mm: 7.2,
        stud_diameter_mm: 4.4,
        brick_height_net_mm: 9.5,
        brick_height_total_mm: 11.2,
      },
      derived_spec_mm: { unit_mm: 8, height_mm: 9.5, knob_diameter_mm: 4.4, knob_height_mm: 1.7 },
      cross_check_warnings: [],
      created_at: "2026-06-06T10:00:00Z",
    });

    await submitCompleteParametricBlock();

    const job = useJobStore.getState().jobs["job-1"];
    expect(job).toMatchObject({
      id: "job-1",
      captureId: "cap-1",
      partId: "p-1",
      status: "pending",
      progress: 0,
      stage: "parametric_generate",
      resultAssetId: null,
      createdAt: "2026-06-06T10:00:00Z",
      imageCount: 2,
      captureMode: "parametric_block",
      pipelineUsed: "parametric_block",
    });
    expect(job?.updatedAt).toEqual(expect.any(String));
    expect(useJobStore.getState().currentJobId).toBe("job-1");
  });

  it("updates the persisted parametric job when the preview SSE completes", async () => {
    let streamHandler: ((event: JobStreamEvent) => void) | null = null;
    subscribeJobMock.mockImplementation((_jobId, opts) => {
      streamHandler = opts.onEvent;
      return () => undefined;
    });
    createParametricBlockMock.mockResolvedValue({
      capture_id: "cap-1",
      job_id: "job-1",
      part_id: "p-1",
      status: "pending",
      mode: "parametric_block",
      system: "lego",
      kind: "tile",
      units_x: 3,
      units_y: 2,
      raw_measurements_mm: {
        outer_pitch_mm: 16,
        inner_pitch_mm: 7.2,
        stud_diameter_mm: 4.4,
        brick_height_net_mm: 9.5,
        brick_height_total_mm: 11.2,
      },
      derived_spec_mm: null,
      cross_check_warnings: [],
      created_at: "2026-06-06T10:00:00Z",
    });

    await submitCompleteParametricBlock();
    expect(subscribeJobMock).toHaveBeenCalledWith("job-1", expect.objectContaining({ onEvent: expect.any(Function) }));

    await act(async () => {
      streamHandler?.({ type: "progress", progress: 65, stage: "parametric_generate" });
      streamHandler?.({ type: "completed", result_asset_id: "asset-1" });
    });

    await waitFor(() =>
      expect(useJobStore.getState().jobs["job-1"]).toMatchObject({
        status: "completed",
        progress: 100,
        stage: "completed",
        resultAssetId: "asset-1",
      }),
    );
  });

  it("lets the user add and remove optional photos on step 1", async () => {
    renderPage();
    const input = screen.getByTestId("photo-input") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(input, {
        target: { files: [makeJpegFile("a.jpg"), makeJpegFile("b.jpg")] },
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("photo-grid").getAttribute("data-count")).toBe("2");
    });
    expect(screen.getByTestId("form-photo-count").textContent).toBe("2");
    // remove one
    await act(async () => {
      fireEvent.click(screen.getByTestId("photo-remove-1"));
    });
    expect(screen.getByTestId("photo-grid").getAttribute("data-count")).toBe("1");
  });

  it("surfaces the cross-check warning when stud_diameter is inconsistent", async () => {
    renderPage();
    // step 1 → 2
    await act(async () => {
      fireEvent.click(screen.getByTestId("step1-next"));
    });
    await waitFor(() => expect(screen.getByTestId("step-kind")).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByTestId("step2-next"));
    });
    await waitFor(() => expect(screen.getByTestId("step-measurements")).toBeInTheDocument());

    // outer=20, inner=4 → expected stud = 8, we type 16 → 8mm off → warning.
    // The wizard's input testids carry the _mm suffix (matches schema keys).
    const outer = screen.getByTestId("measurement-input-outer_pitch_mm") as HTMLInputElement;
    const inner = screen.getByTestId("measurement-input-inner_pitch_mm") as HTMLInputElement;
    const stud = screen.getByTestId("measurement-input-stud_diameter_mm") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(outer, { target: { value: "20" } });
      fireEvent.change(inner, { target: { value: "4" } });
      fireEvent.change(stud, { target: { value: "16" } });
    });
    expect(screen.getByTestId("cross-check-warning")).toBeInTheDocument();
  });

  it("shows the submit error card when createParametricBlock throws", async () => {
    createParametricBlockMock.mockRejectedValue(new Error("network down"));
    renderPage();
    // Walk to step 3 + fill 5 numbers
    await act(async () => {
      fireEvent.click(screen.getByTestId("step1-next"));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("step2-next"));
    });
    await waitFor(() => expect(screen.getByTestId("step-measurements")).toBeInTheDocument());
    for (const [k, v] of Object.entries({
      outer_pitch_mm: 20,
      inner_pitch_mm: 4,
      stud_diameter_mm: 8,
      brick_height_net_mm: 9.6,
      brick_height_total_mm: 11.3,
    })) {
      await act(async () => {
        fireEvent.change(screen.getByTestId(`measurement-input-${k}`), { target: { value: String(v) } });
      });
    }
    await act(async () => {
      fireEvent.click(screen.getByTestId("step3-next"));
    });
    // The error is surfaced as a banner at the top of the page (so the
    // user can re-attempt from the same step, no need to bounce to step 4).
    await waitFor(() => expect(screen.getByTestId("submit-error-banner")).toBeInTheDocument());
    expect(screen.getByTestId("submit-error-banner").textContent).toMatch(/network down/);
  });

  it("the AppHeader exposes a /parametric nav link", () => {
    // Render just the AppHeader — it's a self-contained nav component.
    // The full App tree is integration-tested in the e2e suite.
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AppHeader />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("header-parametric-link")).toBeInTheDocument();
  });
});

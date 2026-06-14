import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { JobList } from "@features/jobs/JobList";
import { useJobStore } from "@stores/useJobStore";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    listCaptures: vi.fn(),
  };
});

import { listCaptures } from "@lib/api";

const listCapturesMock = vi.mocked(listCaptures);

describe("JobList recent captures", () => {
  beforeEach(() => {
    useJobStore.setState({ jobs: {}, currentJobId: null });
    listCapturesMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows backend AR captures needing measurement in a fresh browser context", async () => {
    listCapturesMock.mockResolvedValue([
      {
        capture_id: "cap-ar-1",
        part_id: "ar-brick-1",
        status: "pending",
        image_count: 4,
        created_at: "<PRIVATE_DATE>",
        updated_at: "<PRIVATE_DATE>",
        job_id: null,
        image_keys: ["raw/cap-ar-1/000.png"],
        capture_mode: "phone_walkaround",
        mode: "ar_recognized",
        system: null,
        kind: "brick",
        units_x: null,
        units_y: null,
        recognition_result: { ok: false, reason: "low_confidence" },
        needs_measurement: {
          fields: ["outer_pitch_mm"],
          guidance: "Measure this brick with calipers.",
          endpoint: "/api/v1/parametric-blocks",
          reason: "low_confidence",
        },
      },
    ]);

    render(
      <MemoryRouter>
        <JobList />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("ar-brick-1")).toBeInTheDocument());
    expect(screen.getByText(/low_confidence/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /补充测量/ })).toHaveAttribute("href", "/parametric");
    expect(screen.getByRole("link", { name: /查看采集/ })).toHaveAttribute("href", "/captures/cap-ar-1");
    expect(screen.queryByText("还没有积木模型")).not.toBeInTheDocument();
  });
});

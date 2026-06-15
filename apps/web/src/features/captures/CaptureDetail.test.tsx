import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CaptureDetail } from "@features/captures/CaptureDetail";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    getCapture: vi.fn(),
    getCaptureImages: vi.fn(),
  };
});

import { getCapture, getCaptureImages } from "@lib/api";

const getCaptureMock = vi.mocked(getCapture);
const getCaptureImagesMock = vi.mocked(getCaptureImages);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/captures/:id" element={<CaptureDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CaptureDetail", () => {
  beforeEach(() => {
    getCaptureMock.mockReset();
    getCaptureImagesMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows needs-measurement guidance and raw capture images", async () => {
    getCaptureMock.mockResolvedValue({
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
        fields: ["outer_pitch_mm", "stud_diameter_mm"],
        guidance: "Measure this brick with calipers.",
        endpoint: "/api/v1/parametric-blocks",
        reason: "low_confidence",
      },
    });
    getCaptureImagesMock.mockResolvedValue({
      capture_id: "cap-ar-1",
      images: [
        {
          key: "raw/cap-ar-1/000.png",
          url: "https://raw.example.test/cap-ar-1/000.png?sig=test",
        },
      ],
    });

    renderAt("/captures/cap-ar-1");

    await waitFor(() => expect(screen.getByText("ar-brick-1")).toBeInTheDocument());
    expect(screen.getByText("Measure this brick with calipers.")).toBeInTheDocument();
    expect(screen.getByText(/low_confidence/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /开始测量/ })).toHaveAttribute("href", "/parametric");
    expect(screen.getByRole("img", { name: "raw/cap-ar-1/000.png" })).toHaveAttribute(
      "src",
      "https://raw.example.test/cap-ar-1/000.png?sig=test",
    );
  });
});

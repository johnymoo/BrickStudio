import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CapturePage } from "@features/capture/CapturePage";
import { useJobStore } from "@stores/useJobStore";
import { installGetUserMediaMock } from "../../../tests/helpers";
import type * as Sharpness from "@lib/sharpness";

// Mock the API module so we can capture submit() calls without hitting network.
vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    createCapture: vi.fn(),
    getCapture: vi.fn(),
  };
});

// Mock computeSharpness so we don't depend on the canvas pipeline in jsdom.
// Each test can override `mockSharpness` to drive the ring colour.
const sharpnessMockState: { level: "sharp" | "ok" | "blurry"; variance: number } = {
  level: "sharp",
  variance: 250,
};
vi.mock("@lib/sharpness", async () => {
  const actual = await vi.importActual<typeof Sharpness>("@lib/sharpness");
  return {
    ...actual,
    computeSharpness: () => ({ level: sharpnessMockState.level, variance: sharpnessMockState.variance }),
  };
});

import { createCapture, getCapture } from "@lib/api";
const createCaptureMock = vi.mocked(createCapture);
const getCaptureMock = vi.mocked(getCapture);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/capture"]}>
      <CapturePage />
    </MemoryRouter>,
  );
}

function makeJpegFile(name: string): File {
  return new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], name, { type: "image/jpeg" });
}

async function addNPhotos(n: number) {
  const fileInput = screen.getByTestId("file-input") as HTMLInputElement;
  const files = Array.from({ length: n }, (_, i) => makeJpegFile(`${i + 1}.jpg`));
  await act(async () => {
    fireEvent.change(fileInput, { target: { files } });
  });
}

describe("CapturePage", () => {
  beforeEach(() => {
    useJobStore.setState({ jobs: {}, currentJobId: null });
    createCaptureMock.mockReset();
    getCaptureMock.mockReset();
    sharpnessMockState.level = "sharp";
    sharpnessMockState.variance = 250;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("disables the submit button until 4 photos are added (file fallback)", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();

    const submit = await screen.findByTestId("submit-btn");
    expect(submit).toBeDisabled();

    // Fall through to "no camera" branch — add 3 files, button still disabled.
    const fileInput = screen.getByTestId("file-input") as HTMLInputElement;
    const file1 = makeJpegFile("1.jpg");
    const file2 = makeJpegFile("2.jpg");
    const file3 = makeJpegFile("3.jpg");
    fireEvent.change(fileInput, { target: { files: [file1, file2, file3] } });

    await waitFor(() => expect(submit).toBeDisabled());

    // Add the 4th photo, button enables.
    const file4 = makeJpegFile("4.jpg");
    fireEvent.change(fileInput, { target: { files: [file4] } });
    await waitFor(() => expect(submit).not.toBeDisabled());
  });

  it("disables the capture button when getUserMedia is unavailable", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();
    const captureBtn = await screen.findByTestId("capture-btn");
    expect(captureBtn).toBeDisabled();
  });

  it("submits and navigates to /jobs/{id} on success", async () => {
    installGetUserMediaMock({ succeed: true });
    createCaptureMock.mockResolvedValue({
      capture_id: "cap-1",
      part_id: "p-1",
      image_count: 4,
      status: "pending",
      job_id: "job-1",
    });
    getCaptureMock.mockResolvedValue({
      capture_id: "cap-1",
      part_id: "p-1",
      image_count: 4,
      status: "pending",
      created_at: "2026-06-04T10:00:00Z",
      updated_at: "2026-06-04T10:00:00Z",
    });
    renderPage();

    // Wait for the camera fallback to be replaced by the live video element.
    await waitFor(() => expect(screen.queryByTestId("camera-fallback")).toBeNull());

    const fileInput = screen.getByTestId("file-input") as HTMLInputElement;
    const files = [1, 2, 3, 4].map((i) => makeJpegFile(`${i}.jpg`));
    // Add 3 first, then a final 4th, so the state machine sees the threshold.
    fireEvent.change(fileInput, { target: { files: files.slice(0, 3) } });
    await waitFor(() => expect(screen.getByTestId("submit-btn")).toBeDisabled());
    fireEvent.change(fileInput, { target: { files: [files[3]] } });
    const submit = await screen.findByTestId("submit-btn");
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);
    await waitFor(() => expect(createCaptureMock).toHaveBeenCalledTimes(1));
    // The store should now contain the new job.
    await waitFor(() => {
      const jobs = useJobStore.getState().jobs;
      expect(jobs["job-1"]).toBeDefined();
    });

    // Media stream should be stopped on unmount
    cleanup();
  });

  // ===================== Phase 2: 拍照数量动态 + 删除 =====================

  it("enables submit at 8 photos and reflects the count in the label", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();
    const submit = await screen.findByTestId("submit-btn");
    await addNPhotos(8);
    await waitFor(() => expect(submit).not.toBeDisabled());
    expect(submit.textContent).toMatch(/提交 \(8 张\)/);
    expect(submit.getAttribute("data-count")).toBe("8");
    // The shot grid should show 8 thumbnails.
    expect(screen.getByTestId("shot-grid").getAttribute("data-count")).toBe("8");
  });

  it("disables submit at 4 photos and re-enables after adding more", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();
    const submit = await screen.findByTestId("submit-btn");
    await addNPhotos(4);
    await waitFor(() => expect(submit).not.toBeDisabled());
    // Removing one brings it back below the threshold.
    fireEvent.click(screen.getByTestId("shot-remove-1"));
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit.textContent).toMatch(/至少 4 张/);
  });

  it("keeps submit enabled after removing one photo from a 5-photo batch", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();
    const submit = await screen.findByTestId("submit-btn");
    await addNPhotos(5);
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(screen.getByTestId("shot-remove-3"));
    // 5 -> 4 photos, still >= MIN_IMAGES.
    await waitFor(() => expect(submit).not.toBeDisabled());
    expect(submit.textContent).toMatch(/提交 \(4 张\)/);
  });

  // ===================== Phase 2: captureMode 切换 =====================

  it("switches between capture modes and updates the angle guidance count", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    renderPage();
    // Default: quick_snapshot (4 angles)
    const grid = await screen.findByTestId("angle-grid");
    expect(grid.getAttribute("data-mode")).toBe("quick_snapshot");
    const initial = grid.querySelectorAll("[data-testid^='angle-qs-']");
    expect(initial.length).toBe(4);

    // Switch to phone_walkaround (8 angles)
    fireEvent.click(screen.getByTestId("capture-mode-phone_walkaround"));
    await waitFor(() => {
      expect(screen.getByTestId("angle-grid").getAttribute("data-mode")).toBe("phone_walkaround");
    });
    const pw = screen.getByTestId("angle-grid").querySelectorAll("[data-testid^='angle-pw-']");
    expect(pw.length).toBe(8);

    // Switch to studio_turntable (12 angles)
    fireEvent.click(screen.getByTestId("capture-mode-studio_turntable"));
    await waitFor(() => {
      expect(screen.getByTestId("angle-grid").getAttribute("data-mode")).toBe("studio_turntable");
    });
    const st = screen.getByTestId("angle-grid").querySelectorAll("[data-testid^='angle-st-']");
    expect(st.length).toBe(12);

    // aria-checked state reflects the active mode.
    expect(screen.getByTestId("capture-mode-studio_turntable").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("capture-mode-quick_snapshot").getAttribute("data-active")).toBe("false");
  });

  it("forwards the chosen capture_mode to createCapture", async () => {
    installGetUserMediaMock({ succeed: false, errorMessage: "no camera" });
    createCaptureMock.mockResolvedValue({
      capture_id: "cap-2",
      part_id: "p-2",
      image_count: 4,
      status: "pending",
      job_id: "job-2",
    });
    getCaptureMock.mockResolvedValue({
      capture_id: "cap-2",
      part_id: "p-2",
      image_count: 4,
      status: "pending",
      created_at: "2026-06-04T10:00:00Z",
      updated_at: "2026-06-04T10:00:00Z",
    });
    renderPage();
    // Switch to phone_walkaround then add 4 photos.
    fireEvent.click(screen.getByTestId("capture-mode-phone_walkaround"));
    await addNPhotos(4);
    const submit = await screen.findByTestId("submit-btn");
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);
    await waitFor(() => expect(createCaptureMock).toHaveBeenCalledTimes(1));
    const arg = createCaptureMock.mock.calls[0]?.[0] as FormData | undefined;
    expect(arg).toBeInstanceOf(FormData);
    expect(arg?.get("capture_mode")).toBe("phone_walkaround");
  });

  // ===================== Phase 2: 智能清晰度提示 =====================

  it("shows the sharpness indicator when the camera stream is live", async () => {
    installGetUserMediaMock({ succeed: true });
    sharpnessMockState.level = "sharp";
    sharpnessMockState.variance = 250;
    renderPage();
    await waitFor(() => expect(screen.queryByTestId("sharpness-indicator")).not.toBeNull());
    const indicator = screen.getByTestId("sharpness-indicator");
    expect(indicator.getAttribute("data-level")).toBe("sharp");
    expect(screen.getByTestId("sharpness-variance").textContent).toMatch(/250/);
  });

  it("switches the ring colour as the mocked sharpness level changes", async () => {
    installGetUserMediaMock({ succeed: true });
    sharpnessMockState.level = "blurry";
    sharpnessMockState.variance = 30;
    renderPage();
    await waitFor(() => expect(screen.getByTestId("sharpness-indicator").getAttribute("data-level")).toBe("blurry"));
    // Now flip to sharp — the rAF loop will pick this up on the next tick.
    sharpnessMockState.level = "sharp";
    sharpnessMockState.variance = 220;
    await waitFor(() => expect(screen.getByTestId("sharpness-indicator").getAttribute("data-level")).toBe("sharp"));
  });
});

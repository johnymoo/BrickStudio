import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CapturePage } from "@features/capture/CapturePage";
import { useJobStore } from "@stores/useJobStore";
import { installGetUserMediaMock } from "../../../tests/helpers";

// Mock the API module so we can capture submit() calls without hitting network.
vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return {
    ...actual,
    createCapture: vi.fn(),
    getCapture: vi.fn(),
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

describe("CapturePage", () => {
  beforeEach(() => {
    useJobStore.setState({ jobs: {}, currentJobId: null });
    createCaptureMock.mockReset();
    getCaptureMock.mockReset();
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
    const file1 = new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], "1.jpg", { type: "image/jpeg" });
    const file2 = new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], "2.jpg", { type: "image/jpeg" });
    const file3 = new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], "3.jpg", { type: "image/jpeg" });
    fireEvent.change(fileInput, { target: { files: [file1, file2, file3] } });

    await waitFor(() => expect(submit).toBeDisabled());

    // Add the 4th photo, button enables.
    const file4 = new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], "4.jpg", { type: "image/jpeg" });
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
    const files = [1, 2, 3, 4].map(
      (i) => new File([new Uint8Array([0xff, 0xd8, 0xff, 0, 0, 0])], `${i}.jpg`, { type: "image/jpeg" }),
    );
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
});

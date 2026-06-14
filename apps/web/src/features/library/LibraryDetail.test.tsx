import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { LibraryDetail } from "@features/library/LibraryDetail";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return { ...actual, getLibraryPart: vi.fn(), updateLibraryPart: vi.fn() };
});

// The Viewer pulls in react-three-fiber/WebGL; stub it for jsdom.
vi.mock("@features/viewer/Viewer", () => ({ Viewer: () => <div data-testid="viewer-stub" /> }));

import { getLibraryPart, updateLibraryPart, type LibraryPart } from "@lib/api";

const getMock = vi.mocked(getLibraryPart);
const updateMock = vi.mocked(updateLibraryPart);

const base: LibraryPart = {
  part_id: "p1",
  capture_id: "c1",
  asset_id: "a1",
  source_mode: "parametric_block",
  system: "feile",
  kind: "brick",
  units_x: 2,
  units_y: 2,
  derived_spec_mm: { unit_mm: 16 },
  color: null,
  name: "alpha",
  notes: null,
  status: "pending",
  created_at: "<PRIVATE_DATE>",
  updated_at: "<PRIVATE_DATE>",
};

function renderAt(path = "/library/p1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/library/:id" element={<LibraryDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LibraryDetail", () => {
  beforeEach(() => {
    getMock.mockReset();
    updateMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the part + GLB viewer", async () => {
    getMock.mockResolvedValue(base);
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    expect(screen.getByTestId("viewer-stub")).toBeInTheDocument();
    expect(screen.getByText(/feile/)).toBeInTheDocument();
  });

  it("marks the part verified", async () => {
    getMock.mockResolvedValue(base);
    updateMock.mockResolvedValue({ ...base, status: "verified" });
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("part-verify-btn"));
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("p1", { status: "verified" }, expect.anything()));
  });

  it("saves an edited name", async () => {
    getMock.mockResolvedValue(base);
    updateMock.mockResolvedValue({ ...base, name: "renamed" });
    renderAt();
    await waitFor(() => expect(screen.getByDisplayValue("alpha")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("part-name-input"), { target: { value: "renamed" } });
    fireEvent.click(screen.getByTestId("part-save-btn"));
    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith("p1", expect.objectContaining({ name: "renamed" }), expect.anything()),
    );
  });
});

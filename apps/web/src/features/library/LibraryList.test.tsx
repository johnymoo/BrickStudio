import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { LibraryList } from "@features/library/LibraryList";

vi.mock("@lib/api", async () => {
  const actual = await vi.importActual("@lib/api");
  return { ...actual, listLibraryParts: vi.fn() };
});

import { listLibraryParts, type LibraryPart } from "@lib/api";
const listMock = vi.mocked(listLibraryParts);

function part(over: Partial<LibraryPart>): LibraryPart {
  return {
    part_id: "p",
    capture_id: "c",
    asset_id: "a",
    source_mode: "parametric_block",
    system: "feile",
    kind: "brick",
    units_x: 2,
    units_y: 2,
    derived_spec_mm: null,
    color: null,
    name: "name",
    notes: null,
    status: "pending",
    created_at: "<PRIVATE_DATE>",
    updated_at: "<PRIVATE_DATE>",
    ...over,
  };
}

describe("LibraryList", () => {
  beforeEach(() => listMock.mockReset());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders parts returned by the API", async () => {
    listMock.mockResolvedValue([
      part({ part_id: "p1", name: "alpha" }),
      part({ part_id: "p2", name: "beta", status: "verified" }),
    ]);
    render(
      <MemoryRouter>
        <LibraryList />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());
    expect(screen.getByText("beta")).toBeInTheDocument();
  });

  it("defaults to the pending tab and loads rejected only on its own tab", async () => {
    // Each tab filters by exactly one status (server-side). Default = pending.
    listMock.mockImplementation(async (status?: string) => {
      if (status === "rejected") return [part({ part_id: "pr", name: "trashed", status: "rejected" })];
      if (status === "verified") return [part({ part_id: "pv", name: "blessed", status: "verified" })];
      return [part({ part_id: "p1", name: "alpha", status: "pending" })]; // status === "pending"
    });
    render(
      <MemoryRouter>
        <LibraryList />
      </MemoryRouter>,
    );
    // Default tab loads pending.
    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());
    expect(listMock).toHaveBeenCalledWith("pending", expect.any(Number), expect.anything());
    expect(screen.queryByText("trashed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("library-tab-rejected"));
    await waitFor(() => expect(screen.getByText("trashed")).toBeInTheDocument());
    expect(screen.queryByText("alpha")).not.toBeInTheDocument();
  });
});

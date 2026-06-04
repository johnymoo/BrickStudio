import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Mock matchMedia (not in jsdom, used by some libraries).
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

// Mock ResizeObserver (used by @react-three/fiber Canvas).
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// Mock HTMLCanvasElement.getContext for the 3D canvas in tests.
if (typeof HTMLCanvasElement !== "undefined") {
  if (!HTMLCanvasElement.prototype.getContext) {
    HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(null) as never;
  }
}

// jsdom doesn't ship URL.createObjectURL / revokeObjectURL. The lib.dom types
// already include these methods, so under strict mode the assignment is type
// compatible — we don't need a ts-ignore. We do, however, use `as unknown as`
// to keep the local surface narrow and avoid pulling in DOM lib methods.
if (typeof URL.createObjectURL !== "function") {
  let counter = 0;
  (URL as unknown as { createObjectURL: (b: Blob | MediaSource) => string }).createObjectURL = (blob) => {
    void blob;
    counter += 1;
    return `blob:mock/${counter}`;
  };
  (URL as unknown as { revokeObjectURL: (u: string) => void }).revokeObjectURL = () => undefined;
}

// Shared test setup helpers — mock browser APIs that jsdom doesn't implement.

import { vi } from "vitest";

export function installEventSourceMock() {
  const instances: FakeEventSource[] = [];
  class FakeEventSource {
    public url: string;
    public readyState = 0;
    private listeners: Record<string, Array<(e: unknown) => void>> = {};

    constructor(url: string) {
      this.url = url;
      instances.push(this);
    }
    addEventListener(type: string, cb: (e: unknown) => void) {
      (this.listeners[type] ??= []).push(cb);
    }
    removeEventListener() {
      /* noop */
    }
    close() {
      this.readyState = 2;
    }
    /** Test helper: dispatch an event the listeners should pick up. */
    __dispatch(type: string, data?: unknown) {
      const evt = data !== undefined ? { data: JSON.stringify(data) } : {};
      (this.listeners[type] ?? []).forEach((cb) => cb(evt));
    }
  }

  const Ctor = vi.fn().mockImplementation((url: string) => new FakeEventSource(url));
  // @ts-expect-error -- patching the global for tests
  globalThis.EventSource = Ctor;
  return { instances, EventSource: Ctor };
}

export function installGetUserMediaMock(opts: {
  /** If true, getUserMedia resolves with a fake stream. If false, rejects. */
  succeed?: boolean;
  errorMessage?: string;
} = {}) {
  const { succeed = true, errorMessage = "permission denied" } = opts;
  const mediaStream = {
    getTracks: () => [{ stop: vi.fn() }],
    getVideoTracks: () => [{ stop: vi.fn() }],
    getAudioTracks: () => [],
  };
  const fn = vi.fn();
  if (succeed) {
    fn.mockResolvedValue(mediaStream);
  } else {
    fn.mockRejectedValue(new Error(errorMessage));
  }
  if (typeof navigator === "undefined") {
    // @ts-expect-error -- minimal navigator shim for tests
    globalThis.navigator = {};
  }
  // @ts-expect-error -- mock the getUserMedia surface
  navigator.mediaDevices = { getUserMedia: fn };
  return { fn, mediaStream };
}

export function resetWindowMatchMedia() {
  if (typeof window !== "undefined") {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
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
}

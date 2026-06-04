import { describe, expect, it } from "vitest";
import { formatTimeAgo, formatElapsed } from "@lib/format";

describe("formatTimeAgo", () => {
  it("renders seconds for very recent timestamps", () => {
    const now = new Date().toISOString();
    expect(formatTimeAgo(now)).toMatch(/秒前/);
  });

  it("renders minutes for ~5 min old timestamps", () => {
    const t = new Date(Date.now() - 5 * 60_000).toISOString();
    expect(formatTimeAgo(t)).toMatch(/分钟前/);
  });

  it("renders days for week-old timestamps", () => {
    const t = new Date(Date.now() - 3 * 86_400_000).toISOString();
    expect(formatTimeAgo(t)).toMatch(/天前/);
  });
});

describe("formatElapsed", () => {
  it("formats seconds as mm:ss", () => {
    expect(formatElapsed(65)).toBe("01:05");
  });
  it("switches to h:mm:ss past 1h", () => {
    expect(formatElapsed(3661)).toBe("1:01:01");
  });
  it("clamps negative to 00:00", () => {
    expect(formatElapsed(-5)).toBe("00:00");
  });
});

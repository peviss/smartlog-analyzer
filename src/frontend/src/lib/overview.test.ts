import type { StatsBucket } from "../api/client";
import { change, formatPercent, summarize, windowRange } from "./overview";

const bucket = (service: string | null, level: StatsBucket["log.level"], count: number): StatsBucket => ({
  "service.name": service,
  "log.level": level,
  count,
  first_seen: "2026-11-16T10:00:00Z",
  last_seen: "2026-11-16T11:00:00Z",
});

describe("summarize", () => {
  it("counts errors, error rate and ranks services by errors", () => {
    const overview = summarize(
      [
        bucket("payments", "error", 30),
        bucket("payments", "critical", 5),
        bucket("payments", "info", 65),
        bucket("orders", "warn", 10),
        bucket("orders", "info", 390),
        bucket("auth", "error", 40),
      ],
      [bucket(null, "error", 50), bucket(null, "info", 450)],
    );
    expect(overview.total).toBe(540);
    expect(overview.errors).toBe(75);
    expect(overview.errorRate).toBeCloseTo(75 / 540);
    expect(overview.byLevel).toEqual({ error: 70, critical: 5, info: 455, warn: 10 });
    expect(overview.services.map((s) => [s.service, s.errors])).toEqual([
      ["auth", 40],
      ["payments", 35],
      ["orders", 0],
    ]);
    expect(overview.servicesWithErrors).toBe(2);
    expect([overview.previousTotal, overview.previousErrors]).toEqual([500, 50]);
    expect(overview.previousErrorRate).toBeCloseTo(0.1);
  });

  it("handles an empty period", () => {
    const overview = summarize([], []);
    expect(overview).toMatchObject({ total: 0, errors: 0, errorRate: 0, servicesWithErrors: 0, services: [] });
  });
});

describe("change", () => {
  it.each([
    [150, 100, 0.5],
    [50, 100, -0.5],
    [0, 0, 0],
  ])("%i vs %i", (current, previous, expected) => {
    expect(change(current, previous)).toBe(expected);
  });

  it("has no comparison when the previous period was empty", () => {
    expect(change(10, 0)).toBeNull();
  });
});

it("builds the window and the previous period of the same length", () => {
  const now = new Date("2026-11-16T12:00:00Z");
  expect(windowRange("24h", now)).toEqual({
    from: "2026-11-15T12:00:00.000Z",
    to: "2026-11-16T12:00:00.000Z",
    previousFrom: "2026-11-14T12:00:00.000Z",
  });
});

it("formats percentages without a trailing .0", () => {
  expect(formatPercent(0.25)).toBe("25 %");
  expect(formatPercent(0.1234)).toBe("12.3 %");
});

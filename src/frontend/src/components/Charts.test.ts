import { fillGaps } from "./Charts";

it("fills hours without logs with empty buckets", () => {
  const filled = fillGaps(
    [
      { period: "2026-11-16T10:00:00.000Z", values: { error: 2 } },
      { period: "2026-11-16T13:00:00.000Z", values: { error: 1 } },
    ],
    "hour",
  );
  expect(filled.map((p) => p.period)).toEqual([
    "2026-11-16T10:00:00.000Z",
    "2026-11-16T11:00:00.000Z",
    "2026-11-16T12:00:00.000Z",
    "2026-11-16T13:00:00.000Z",
  ]);
  expect(filled[1].values).toEqual({});
});

it("leaves a single point alone", () => {
  expect(fillGaps([{ period: "2026-11-16T10:00:00Z", values: {} }], "day")).toHaveLength(1);
});

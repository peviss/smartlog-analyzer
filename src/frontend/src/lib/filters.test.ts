import { describeFilters, EMPTY_FILTERS, isoToLocal, localToIso, toQuery } from "./filters";

describe("toQuery", () => {
  it("leaves out empty filters", () => {
    expect(toQuery(EMPTY_FILTERS)).toEqual({});
  });

  it("uses ECS parameter names and normalizes services", () => {
    const query = toQuery({
      ...EMPTY_FILTERS,
      services: " Payments-API, orders ,",
      levels: ["error", "critical"],
      traceId: " abc ",
      q: " refused ",
    });
    expect(query).toEqual({
      "service.name": ["payments-api", "orders"],
      "log.level": ["error", "critical"],
      "trace.id": "abc",
      q: "refused",
    });
  });

  it("sends dates as UTC ISO and ignores invalid ones", () => {
    const local = "2026-11-16T10:30";
    expect(toQuery({ ...EMPTY_FILTERS, from: local, to: "garbage" })).toEqual({
      from: new Date(local).toISOString(),
    });
  });
});

describe("date helpers", () => {
  it("round-trips local datetime values", () => {
    expect(isoToLocal(localToIso("2026-11-16T10:30")!)).toBe("2026-11-16T10:30");
  });
});

describe("describeFilters", () => {
  it("explains when nothing is filtered", () => {
    expect(describeFilters(EMPTY_FILTERS)).toBe("Sin filtros: todos los logs");
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../api/client";
import { EMPTY_FILTERS } from "../lib/filters";
import { LogsPage } from "./LogsPage";

function makeLog(id: number, overrides: Partial<api.LogRead> = {}): api.LogRead {
  return {
    id,
    "@timestamp": "2026-11-16T10:00:00Z",
    log: { level: "ERROR" },
    message: `Payment failed for order ${id}`,
    service: { name: "payments-api" },
    event: { severity: 3, ingested: "2026-11-16T10:00:01Z" },
    ecs: { version: "9.4.0" },
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

it("lists logs, marks processing tags and analyzes the selected ones", async () => {
  vi.spyOn(api, "listLogs").mockResolvedValue({
    items: [makeLog(1), makeLog(2, { tags: ["_level_unrecognized"] })],
    page: { total: 2, limit: 50, offset: 0, has_more: false },
  });
  const onAnalyzeIds = vi.fn();
  render(
    <LogsPage filters={EMPTY_FILTERS} onFiltersChange={vi.fn()} onAnalyzeIds={onAnalyzeIds} onAnalyzeFilters={vi.fn()} />,
  );

  expect(await screen.findByText("Payment failed for order 1")).toBeInTheDocument();
  expect(screen.getByText("_level_unrecognized")).toHaveClass("tag-processing");
  expect(screen.getByText("1–2 de 2")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("checkbox", { name: "Seleccionar log 2" }));
  await userEvent.click(screen.getByRole("button", { name: "Analizar seleccionados (1)" }));
  expect(onAnalyzeIds).toHaveBeenCalledWith([2]);
});

it("sends the applied filters with ECS names", async () => {
  const listLogs = vi.spyOn(api, "listLogs").mockResolvedValue({
    items: [],
    page: { total: 0, limit: 50, offset: 0, has_more: false },
  });
  const onFiltersChange = vi.fn();
  const { rerender } = render(
    <LogsPage filters={EMPTY_FILTERS} onFiltersChange={onFiltersChange} onAnalyzeIds={vi.fn()} onAnalyzeFilters={vi.fn()} />,
  );
  expect(await screen.findByText("No hay logs que coincidan con los filtros.")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "error" }));
  await userEvent.click(screen.getByRole("button", { name: "Aplicar" }));
  const applied = onFiltersChange.mock.calls[0][0];
  expect(applied.levels).toEqual(["error"]);

  rerender(
    <LogsPage filters={applied} onFiltersChange={onFiltersChange} onAnalyzeIds={vi.fn()} onAnalyzeFilters={vi.fn()} />,
  );
  await waitFor(() =>
    expect(listLogs).toHaveBeenLastCalledWith({ "log.level": ["error"], limit: 50, offset: 0 }, expect.anything()),
  );
});

it("shows API errors with the request id", async () => {
  vi.spyOn(api, "listLogs").mockRejectedValue(
    new api.ApiError(500, "INTERNAL_ERROR", "Unexpected error.", undefined, "req-42"),
  );
  render(<LogsPage filters={EMPTY_FILTERS} onFiltersChange={vi.fn()} onAnalyzeIds={vi.fn()} onAnalyzeFilters={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("req-42");
});

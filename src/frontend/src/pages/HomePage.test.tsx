import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../api/client";
import { HomePage } from "./HomePage";

const bucket = (fields: Partial<api.StatsBucket>, count: number): api.StatsBucket => ({
  count,
  first_seen: "2026-11-16T10:00:00Z",
  last_seen: "2026-11-16T11:00:00Z",
  ...fields,
});

function mockApi() {
  vi.spyOn(api, "getStats").mockImplementation(async (query) => {
    const groups = query.group_by ?? [];
    if (groups.includes("service.name")) {
      return {
        group_by: groups,
        total: 200,
        buckets: [
          bucket({ "service.name": "payments-api", "log.level": "error" }, 30),
          bucket({ "service.name": "payments-api", "log.level": "critical" }, 10),
          bucket({ "service.name": "payments-api", "log.level": "info" }, 60),
          bucket({ "service.name": "orders", "log.level": "info" }, 100),
        ],
      };
    }
    if (groups.includes("hour")) {
      return {
        group_by: groups,
        total: 40,
        buckets: [bucket({ "log.level": "error", period: "2026-11-16T10:00:00Z" }, 40)],
      };
    }
    // previous period
    return { group_by: groups, total: 100, buckets: [bucket({ "log.level": "error" }, 20), bucket({ "log.level": "info" }, 80)] };
  });
  vi.spyOn(api, "listLogs").mockResolvedValue({
    items: [
      {
        id: 7,
        "@timestamp": "2026-11-16T11:00:00Z",
        log: { level: "ERROR" },
        message: "Could not acquire database connection",
        service: { name: "payments-api" },
        event: { severity: 3, ingested: "2026-11-16T11:00:01Z" },
        ecs: { version: "9.4.0" },
      },
    ],
    page: { total: 1, limit: 5, offset: 0, has_more: false },
  });
}

afterEach(() => vi.restoreAllMocks());

function renderHome() {
  const props = { onOpenLogs: vi.fn(), onOpenStats: vi.fn(), onAnalyzePeriod: vi.fn(), onAnalyzeIds: vi.fn() };
  render(<HomePage {...props} />);
  return props;
}

it("shows the key numbers of the period with the change vs the previous one", async () => {
  mockApi();
  renderHome();

  const errors = await screen.findByRole("button", { name: /Errores y críticos: 40/ });
  expect(within(errors).getByText("10 críticos")).toBeInTheDocument();
  expect(within(errors).getByText(/▲ 100 % vs período anterior/)).toHaveClass("delta-bad");
  expect(screen.getByRole("button", { name: /Logs: 200/ })).toHaveTextContent("▲ 100 % vs período anterior");
  expect(screen.getByRole("button", { name: /Tasa de error: 20 %/ })).toHaveTextContent("igual que el período anterior");
  expect(screen.getByRole("button", { name: /Servicios con errores: 1 de 2/ })).toHaveTextContent(
    "más afectado: payments-api",
  );
  expect(screen.getByText("Could not acquire database connection")).toBeInTheDocument();
});

it("links each number to its details with the period's filters", async () => {
  mockApi();
  const props = renderHome();

  await userEvent.click(await screen.findByRole("button", { name: /Errores y críticos/ }));
  const filters = props.onOpenLogs.mock.calls[0][0];
  expect(filters.levels).toEqual(["critical", "error"]);
  expect(filters.from).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);

  await userEvent.click(screen.getByRole("button", { name: "payments-api" }));
  expect(props.onOpenLogs.mock.calls[1][0]).toMatchObject({ services: "payments-api", levels: ["critical", "error"] });

  await userEvent.click(screen.getByRole("button", { name: /Tasa de error/ }));
  expect(props.onOpenStats).toHaveBeenCalledTimes(1);

  await userEvent.click(screen.getByRole("button", { name: "Analizar este período con IA" }));
  expect(props.onAnalyzePeriod.mock.calls[0][0].levels).toEqual(["critical", "error", "warn"]);
});

it("changes the period and asks for the matching window", async () => {
  mockApi();
  renderHome();
  await screen.findByRole("button", { name: /Logs: 200/ });

  await userEvent.click(screen.getByLabelText("Últimos 30 días"));
  const lastSeries = vi.mocked(api.getStats).mock.calls.at(-1)?.[0];
  expect(lastSeries?.group_by).toEqual(["log.level", "day"]);
});

it("explains an empty period", async () => {
  vi.spyOn(api, "getStats").mockResolvedValue({ group_by: [], total: 0, buckets: [] });
  vi.spyOn(api, "listLogs").mockResolvedValue({ items: [], page: { total: 0, limit: 5, offset: 0, has_more: false } });
  renderHome();
  expect(await screen.findByText("No hay logs en este período.")).toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "../api/client";
import { ImportPanel } from "./ImportPanel";

afterEach(() => vi.restoreAllMocks());

it("imports pasted text with the defaults and reports rejected lines", async () => {
  const importLogs = vi.spyOn(api, "importLogs").mockResolvedValue({
    ingested: 1,
    ids: [10],
    duplicates: 0,
    failed: 1,
    errors: [{ index: 1, line: 3, errors: [{ field: "service.name", location: "body", message: "Field required", type: "missing" }] }],
    format: "text",
    records: 2,
  });
  render(<ImportPanel />);

  await userEvent.type(screen.getByLabelText("…o pegá el contenido"), "2026-11-16T10:00:00Z ERROR boom");
  await userEvent.type(screen.getByLabelText("service.name si falta"), "payments-api");
  await userEvent.selectOptions(screen.getByLabelText("log.level si falta"), "warn");
  await userEvent.click(screen.getByRole("button", { name: "Importar" }));

  expect(importLogs).toHaveBeenCalledWith("2026-11-16T10:00:00Z ERROR boom", {
    format: "text",
    serviceName: "payments-api",
    logLevel: "warn",
  });
  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("1 guardados · 0 duplicados · 1 rechazados");
  expect(status).toHaveTextContent("Línea 3: service.name Field required");
});

it("detects CSV from the file extension", async () => {
  const importLogs = vi.spyOn(api, "importLogs").mockResolvedValue({
    ingested: 1, ids: [1], duplicates: 0, failed: 0, errors: [], format: "csv", records: 1,
  });
  render(<ImportPanel />);
  const file = new File(["message,level\nhola,info\n"], "export.csv", { type: "text/csv" });
  await userEvent.upload(screen.getByLabelText("Archivo de logs"), file);

  expect(await screen.findByText("export.csv")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Importar" }));
  expect(importLogs).toHaveBeenCalledWith("message,level\nhola,info\n", expect.objectContaining({ format: "csv" }));
});

it("blocks files over the size limit before uploading", async () => {
  const importLogs = vi.spyOn(api, "importLogs");
  render(<ImportPanel />);
  const big = new File(["x"], "huge.log");
  Object.defineProperty(big, "size", { value: 6 * 1024 * 1024 });
  await userEvent.upload(screen.getByLabelText("Archivo de logs"), big);

  expect(await screen.findByText(/el máximo es 5.0 MB/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Importar" })).toBeDisabled();
  expect(importLogs).not.toHaveBeenCalled();
});

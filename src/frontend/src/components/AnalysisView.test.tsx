import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { AnalysisRead } from "../api/client";
import { AnalysisView } from "./AnalysisView";

const analysis: AnalysisRead = {
  id: "7f9c",
  status: "completed",
  created_at: "2026-11-16T10:00:00Z",
  completed_at: "2026-11-16T10:00:02Z",
  request: {},
  log_count: 40,
  pattern_count: 3,
  stats: { total: 40, by_level: { error: 30, warn: 10 }, by_service: { payments: 40 }, window: null },
  provider: "mock",
  model: "mock-rules-v1",
  summary: "Payments cannot reach the database.",
  categories: [
    {
      name: "Slow requests",
      severity: "medium",
      description: "Timeouts",
      occurrences: 10,
      services: ["payments"],
      example_log_ids: [],
    },
    {
      name: "Database connectivity",
      severity: "critical",
      description: "Connection refused",
      occurrences: 30,
      services: ["payments"],
      example_log_ids: [12, 13],
    },
  ],
  suggestions: [{ title: "Check the pool", description: "Raise max connections", priority: "high", category: null }],
  error: null,
  duration_ms: 1500,
};

it("shows the summary, the most severe problem first and its example logs", async () => {
  const onOpenLog = vi.fn();
  render(<AnalysisView analysis={analysis} onOpenLog={onOpenLog} />);

  expect(screen.getByText("Payments cannot reach the database.")).toBeInTheDocument();
  const headings = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
  expect(headings.slice(0, 2)).toEqual(["Database connectivity", "Slow requests"]);
  expect(screen.getByText("Check the pool")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "#13" }));
  expect(onOpenLog).toHaveBeenCalledWith(13);
});

it("says so when there are no problems", () => {
  render(<AnalysisView analysis={{ ...analysis, categories: [], suggestions: [] }} onOpenLog={vi.fn()} />);
  expect(screen.getByText("No se encontraron problemas en estos logs.")).toBeInTheDocument();
});

it("shows the error of a failed analysis", () => {
  render(
    <AnalysisView
      analysis={{ ...analysis, status: "failed", summary: null, categories: null, suggestions: null, error: { code: "LLM_TIMEOUT", message: "late" } }}
      onOpenLog={vi.fn()}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("LLM_TIMEOUT");
});

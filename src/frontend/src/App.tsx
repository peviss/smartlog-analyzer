import { useState } from "react";

import { getHealth } from "./api/client";
import { EMPTY_FILTERS, type Filters } from "./lib/filters";
import { navigate, type Route, routeToHash, useRoute } from "./lib/router";
import { useApi } from "./lib/useApi";
import { type AnalysisDraft, AnalysisPage } from "./pages/AnalysisPage";
import { IngestPage } from "./pages/IngestPage";
import { LogsPage } from "./pages/LogsPage";
import { StatsPage } from "./pages/StatsPage";

const NAV: { route: Route; label: string }[] = [
  { route: { page: "logs" }, label: "Logs" },
  { route: { page: "stats" }, label: "Estadísticas" },
  { route: { page: "analysis" }, label: "Análisis" },
  { route: { page: "ingest" }, label: "Ingesta" },
];

function HealthIndicator({ provider, status }: { provider?: string; status: "ok" | "degraded" | "down" | "loading" }) {
  const text = {
    ok: "API y base de datos OK",
    degraded: "Base de datos no disponible",
    down: "API no disponible",
    loading: "Conectando…",
  }[status];
  return (
    <span className={`health health-${status}`} title={text}>
      <i aria-hidden="true" />
      <span className="health-text">{text}</span>
      {provider && <span className="badge neutral">LLM: {provider}</span>}
    </span>
  );
}

export function App() {
  const route = useRoute();
  // Filters are shared: what you narrow down in Logs is what Stats shows and Analysis sends.
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [draft, setDraft] = useState<AnalysisDraft | null>(null);
  const health = useApi((signal) => getHealth(signal), "health");
  const healthStatus = health.loading
    ? "loading"
    : health.error
      ? "down"
      : (health.data?.status ?? "down");

  const startAnalysis = (next: AnalysisDraft) => {
    setDraft(next);
    navigate({ page: "analysis" });
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Saltar al contenido
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" href="#/logs">
            <svg viewBox="0 0 32 32" aria-hidden="true">
              <rect width="32" height="32" rx="7" />
              <path d="M8 10h16M8 16h11M8 22h14" />
            </svg>
            SmartLog
          </a>
          <nav aria-label="Secciones">
            {NAV.map(({ route: target, label }) => (
              <a
                key={label}
                href={routeToHash(target)}
                aria-current={route.page === target.page ? "page" : undefined}
              >
                {label}
              </a>
            ))}
          </nav>
          <HealthIndicator status={healthStatus} provider={health.data?.llm_provider} />
        </div>
      </header>

      <main id="main" className="content">
        {route.page === "logs" && (
          <LogsPage
            filters={filters}
            onFiltersChange={setFilters}
            onAnalyzeIds={(ids) => startAnalysis({ mode: "ids", ids })}
            onAnalyzeFilters={() => startAnalysis({ mode: "filters" })}
          />
        )}
        {route.page === "stats" && <StatsPage filters={filters} onFiltersChange={setFilters} />}
        {route.page === "analysis" && (
          <AnalysisPage
            analysisId={route.id}
            filters={filters}
            draft={draft}
            llmProvider={health.data?.llm_provider}
            onFiltersChange={setFilters}
          />
        )}
        {route.page === "ingest" && <IngestPage />}
      </main>

      <footer className="footer muted small">
        SmartLog Analyzer {health.data?.version && `v${health.data.version}`} ·{" "}
        <a href="/docs" target="_blank" rel="noreferrer">
          API (OpenAPI)
        </a>
      </footer>
    </div>
  );
}

import { type FormEvent, useEffect, useState } from "react";

import { analyze, ApiError, type AnalysisRead, getAnalysis, getLog, type LogRead } from "../api/client";
import { StatusBadge } from "../components/Badges";
import { AnalysisView } from "../components/AnalysisView";
import { ErrorBanner } from "../components/ErrorBanner";
import { LogDetail } from "../components/LogDetail";
import { describeFilters, type Filters, toQuery } from "../lib/filters";
import { formatDateTime } from "../lib/format";
import { navigate } from "../lib/router";
import { loadRecent, type RecentAnalysis, rememberAnalysis } from "../lib/storage";

export type AnalysisDraft = { mode: "filters" } | { mode: "ids"; ids: number[] };

interface Props {
  analysisId?: string;
  filters: Filters;
  draft: AnalysisDraft | null;
  llmProvider?: string;
  onFiltersChange: (filters: Filters) => void;
}

const MAX_LOGS = 500;

function parseIds(text: string): number[] {
  return [...new Set(text.split(/[\s,;]+/).filter(Boolean).map(Number))].filter(
    (n) => Number.isInteger(n) && n > 0,
  );
}

export function AnalysisPage({ analysisId, filters, draft, llmProvider, onFiltersChange }: Props) {
  const [mode, setMode] = useState<"filters" | "ids">(draft?.mode ?? "filters");
  const [idsText, setIdsText] = useState(draft?.mode === "ids" ? draft.ids.join(", ") : "");
  const [limit, setLimit] = useState(200);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);
  const [result, setResult] = useState<AnalysisRead | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [recent, setRecent] = useState<RecentAnalysis[]>(loadRecent);
  const [lookup, setLookup] = useState("");
  const [openLog, setOpenLog] = useState<LogRead | null>(null);
  const [logError, setLogError] = useState<unknown>(null);

  // A draft coming from the logs page replaces the form.
  useEffect(() => {
    if (!draft) return;
    setMode(draft.mode);
    if (draft.mode === "ids") setIdsText(draft.ids.join(", "));
  }, [draft]);

  // The URL (#/analysis/<id>) decides which analysis is shown.
  useEffect(() => {
    if (!analysisId) {
      setResult(null);
      return;
    }
    if (result?.id === analysisId) return;
    const controller = new AbortController();
    setLoadError(null);
    getAnalysis(analysisId, controller.signal).then(
      (a) => {
        setResult(a);
        setRecent(rememberAnalysis(a));
      },
      (e: unknown) => !controller.signal.aborted && setLoadError(e),
    );
    return () => controller.abort();
  }, [analysisId, result?.id]);

  const ids = parseIds(idsText);
  const canRun =
    !running && (mode === "filters" ? limit >= 1 && limit <= MAX_LOGS : ids.length > 0 && ids.length <= MAX_LOGS);

  const run = async (event: FormEvent) => {
    event.preventDefault();
    setRunning(true);
    setRunError(null);
    try {
      const analysis = await analyze(
        mode === "ids" ? { log_ids: ids } : { filters: { ...toQuery(filters), limit } },
      );
      setResult(analysis);
      setRecent(rememberAnalysis(analysis));
      navigate({ page: "analysis", id: analysis.id });
    } catch (error) {
      setRunError(error);
      // A failed LLM call is still persisted; show it so its error and stats are visible.
      const failedId = error instanceof ApiError ? error.extra.analysis_id : undefined;
      if (typeof failedId === "string") navigate({ page: "analysis", id: failedId });
    } finally {
      setRunning(false);
    }
  };

  const showLog = (id: number) => {
    setLogError(null);
    getLog(id).then(setOpenLog, setLogError);
  };

  return (
    <section aria-labelledby="analysis-page-title" className="analysis-layout">
      <div className="analysis-main">
        <div className="page-head">
          <h1 id="analysis-page-title">Análisis con IA</h1>
        </div>

        <form className="card analyze-form" onSubmit={run}>
          <fieldset className="segmented" aria-label="Qué logs analizar">
            <label className={mode === "filters" ? "selected" : ""}>
              <input type="radio" name="mode" checked={mode === "filters"} onChange={() => setMode("filters")} />
              Por filtros
            </label>
            <label className={mode === "ids" ? "selected" : ""}>
              <input type="radio" name="mode" checked={mode === "ids"} onChange={() => setMode("ids")} />
              Por ids
            </label>
          </fieldset>

          {mode === "filters" ? (
            <div className="analyze-filters">
              <p>
                <span className="muted">Filtros actuales:</span> {describeFilters(filters)}{" "}
                <a href="#/logs">Cambiar en Logs</a>
              </p>
              <label className="field narrow">
                <span>Máximo de logs (los más recientes)</span>
                <input
                  type="number"
                  min={1}
                  max={MAX_LOGS}
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                />
              </label>
            </div>
          ) : (
            <label className="field">
              <span>Ids de logs (separados por coma o espacio, hasta {MAX_LOGS})</span>
              <textarea rows={3} value={idsText} onChange={(e) => setIdsText(e.target.value)} placeholder="12, 45, 78" />
              <span className="muted small">{ids.length} ids válidos</span>
            </label>
          )}

          <div className="form-actions">
            {llmProvider && (
              <span className="muted small">
                Modelo: <strong>{llmProvider}</strong>
                {llmProvider !== "mock" && " · cada análisis consume tokens del proveedor"}
              </span>
            )}
            <span className="spacer" />
            <button className="button primary" type="submit" disabled={!canRun}>
              {running ? "Analizando…" : "Analizar"}
            </button>
          </div>
          {running && (
            <p className="muted small" role="status">
              Agrupando patrones y consultando al modelo. Puede tardar hasta el timeout configurado.
            </p>
          )}
        </form>

        <ErrorBanner error={runError} title="El análisis no se pudo completar" />
        <ErrorBanner error={loadError} title="No se pudo cargar el análisis" />
        <ErrorBanner error={logError} title="No se pudo cargar el log" />

        {result && <AnalysisView analysis={result} onOpenLog={showLog} />}
      </div>

      <aside className="card recent" aria-labelledby="recent-title">
        <h2 id="recent-title">Análisis recientes</h2>
        <form
          className="lookup"
          onSubmit={(e) => {
            e.preventDefault();
            if (lookup.trim()) navigate({ page: "analysis", id: lookup.trim() });
          }}
        >
          <input
            aria-label="Abrir análisis por id"
            placeholder="Abrir por id…"
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
          />
        </form>
        {recent.length === 0 ? (
          <p className="muted small">Los análisis que corras desde este navegador aparecen acá.</p>
        ) : (
          <ul>
            {recent.map((r) => (
              <li key={r.id} className={r.id === analysisId ? "current" : undefined}>
                <a href={`#/analysis/${r.id}`}>
                  <span className="recent-head">
                    <StatusBadge status={r.status} />
                    <span className="muted small">{formatDateTime(r.createdAt)}</span>
                  </span>
                  <span className="recent-summary">{r.summary ?? `${r.logCount} logs`}</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </aside>

      {openLog && (
        <LogDetail
          log={openLog}
          onClose={() => setOpenLog(null)}
          onAnalyze={(id) => {
            setOpenLog(null);
            setMode("ids");
            setIdsText(String(id));
          }}
          onFilterTrace={(traceId) => {
            setOpenLog(null);
            onFiltersChange({ ...filters, traceId });
            navigate({ page: "logs" });
          }}
        />
      )}
    </section>
  );
}

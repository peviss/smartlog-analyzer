import type { AnalysisRead, Level } from "../api/client";
import { LEVELS } from "../lib/filters";
import { formatDateTime, formatDuration, formatNumber } from "../lib/format";
import { SeverityBadge, StatusBadge } from "./Badges";
import { BarList } from "./Charts";

interface Stats {
  total?: number;
  by_level?: Record<string, number>;
  by_service?: Record<string, number>;
  window?: { from: string; to: string } | null;
}

interface Props {
  analysis: AnalysisRead;
  onOpenLog: (id: number) => void;
}

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 } as const;

export function AnalysisView({ analysis, onOpenLog }: Props) {
  const stats = analysis.stats as Stats;
  const categories = [...(analysis.categories ?? [])].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || b.occurrences - a.occurrences,
  );
  const suggestions = [...(analysis.suggestions ?? [])].sort(
    (a, b) => SEVERITY_ORDER[a.priority] - SEVERITY_ORDER[b.priority],
  );

  return (
    <article className="analysis" aria-labelledby="analysis-title">
      <header className="analysis-header">
        <div>
          <h2 id="analysis-title">
            Análisis <StatusBadge status={analysis.status} />
          </h2>
          <p className="muted small">
            {formatDateTime(analysis.created_at)} · {formatNumber(analysis.log_count)} logs en{" "}
            {formatNumber(analysis.pattern_count)} patrones · {analysis.provider}/{analysis.model} ·{" "}
            {formatDuration(analysis.duration_ms)}
          </p>
        </div>
        <code className="muted small">{analysis.id}</code>
      </header>

      {analysis.error && (
        <div className="banner banner-error" role="alert">
          <strong>{analysis.error.code}</strong>
          <p>{analysis.error.message}</p>
        </div>
      )}

      {analysis.summary && (
        <section className="card summary">
          <h3>Resumen</h3>
          <p>{analysis.summary}</p>
        </section>
      )}

      {analysis.status === "completed" && categories.length === 0 && (
        <p className="banner banner-ok">No se encontraron problemas en estos logs.</p>
      )}

      {categories.length > 0 && (
        <section>
          <h3>Problemas encontrados</h3>
          <div className="category-grid">
            {categories.map((c) => (
              <div key={c.name} className={`card category sev-border-${c.severity}`}>
                <div className="category-head">
                  <h4>{c.name}</h4>
                  <SeverityBadge value={c.severity} />
                </div>
                <p>{c.description}</p>
                <p className="muted small">
                  {formatNumber(c.occurrences)} ocurrencias
                  {c.services?.length ? ` · ${c.services.join(", ")}` : ""}
                </p>
                {c.example_log_ids?.length ? (
                  <p className="example-logs small">
                    Ejemplos:{" "}
                    {c.example_log_ids.map((id) => (
                      <button key={id} className="link-button" onClick={() => onOpenLog(id)}>
                        #{id}
                      </button>
                    ))}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      )}

      {suggestions.length > 0 && (
        <section>
          <h3>Acciones sugeridas</h3>
          <ol className="suggestions">
            {suggestions.map((s, i) => (
              <li key={i} className="card">
                <div className="category-head">
                  <h4>{s.title}</h4>
                  <SeverityBadge value={s.priority} prefix="prioridad" />
                </div>
                <p>{s.description}</p>
                {s.category && <p className="muted small">Sobre: {s.category}</p>}
              </li>
            ))}
          </ol>
        </section>
      )}

      {stats?.total ? (
        <section className="stats-grid">
          <div className="card">
            <h3>Por nivel</h3>
            <BarList
              ariaLabel="Logs analizados por nivel"
              data={LEVELS.filter((l) => stats.by_level?.[l]).map((l: Level) => ({
                label: l,
                value: stats.by_level?.[l] ?? 0,
                tone: `level-${l}`,
              }))}
            />
          </div>
          <div className="card">
            <h3>Por servicio</h3>
            <BarList
              ariaLabel="Logs analizados por servicio"
              data={Object.entries(stats.by_service ?? {}).map(([label, value]) => ({ label, value }))}
            />
          </div>
          {stats.window && (
            <p className="muted small span-all">
              Ventana: {formatDateTime(stats.window.from)} → {formatDateTime(stats.window.to)}
            </p>
          )}
        </section>
      ) : null}
    </article>
  );
}

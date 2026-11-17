import { useMemo, useState } from "react";

import { getStats, type Level, listLogs, type LogRead } from "../api/client";
import { LevelBadge, StatusBadge } from "../components/Badges";
import { BarList, fillGaps, TimeSeriesChart, type TimeSeriesPoint } from "../components/Charts";
import { ErrorBanner } from "../components/ErrorBanner";
import { LogDetail } from "../components/LogDetail";
import { EMPTY_FILTERS, type Filters, isoToLocal } from "../lib/filters";
import { formatDateTime, formatNumber, formatPeriod, levelFromSeverity } from "../lib/format";
import {
  change,
  ERROR_LEVELS,
  formatPercent,
  type Overview,
  type OverviewWindow,
  summarize,
  WINDOWS,
  windowRange,
} from "../lib/overview";
import { loadRecent } from "../lib/storage";
import { useApi } from "../lib/useApi";

const CHART_LEVELS: Level[] = ["critical", "error", "warn"];
const TOP_SERVICES = 5;
const LATEST_ERRORS = 5;

interface Props {
  onOpenLogs: (filters: Filters) => void;
  onOpenStats: (filters: Filters) => void;
  onAnalyzePeriod: (filters: Filters) => void;
  onAnalyzeIds: (ids: number[]) => void;
}

interface HomeData {
  overview: Overview;
  points: TimeSeriesPoint[];
  latestErrors: LogRead[];
}

/** Change vs the previous period. `goodWhenDown`: fewer is better (errors). */
function Delta({ value, goodWhenDown, label }: { value: number | null; goodWhenDown?: boolean; label?: string }) {
  if (value === null) return <span className="delta muted">sin datos del período anterior</span>;
  if (Math.abs(value) < 0.005) return <span className="delta muted">igual que el período anterior</span>;
  const up = value > 0;
  const tone = goodWhenDown === undefined ? "neutral" : up === goodWhenDown ? "bad" : "good";
  return (
    <span className={`delta delta-${tone}`}>
      {up ? "▲" : "▼"} {label ?? formatPercent(Math.abs(value), 0)} vs período anterior
    </span>
  );
}

function Kpi({
  title,
  value,
  detail,
  delta,
  onOpen,
  linkLabel,
  tone,
}: {
  title: string;
  value: string;
  detail?: React.ReactNode;
  delta: React.ReactNode;
  onOpen: () => void;
  linkLabel: string;
  tone?: "danger";
}) {
  return (
    <button type="button" className={`kpi card${tone ? ` kpi-${tone}` : ""}`} onClick={onOpen} aria-label={`${title}: ${value}. ${linkLabel}`}>
      <span className="kpi-title">{title}</span>
      <span className="kpi-value">{value}</span>
      {detail && <span className="kpi-detail">{detail}</span>}
      {delta}
      <span className="kpi-link">{linkLabel} →</span>
    </button>
  );
}

export function HomePage({ onOpenLogs, onOpenStats, onAnalyzePeriod, onAnalyzeIds }: Props) {
  const [span, setSpan] = useState<OverviewWindow>("24h");
  const [refresh, setRefresh] = useState(0);
  const [openLog, setOpenLog] = useState<LogRead | null>(null);
  // Fixed per selection/refresh, so the request key is stable between renders.
  const range = useMemo(() => windowRange(span), [span, refresh]);
  const bucket = WINDOWS[span].bucket;

  const { data, error, loading } = useApi<HomeData>(
    async (signal) => {
      const [current, previous, series, latest] = await Promise.all([
        getStats({ from: range.from, to: range.to, group_by: ["service.name", "log.level"] }, signal),
        getStats({ from: range.previousFrom, to: range.from, group_by: ["log.level"] }, signal),
        getStats({ from: range.from, to: range.to, group_by: ["log.level", bucket] }, signal),
        listLogs({ "log.level": ERROR_LEVELS, from: range.from, to: range.to, limit: LATEST_ERRORS, offset: 0 }, signal),
      ]);
      const byPeriod = new Map<string, Record<string, number>>();
      for (const b of series.buckets) {
        const level = b["log.level"];
        if (!b.period || !level || !CHART_LEVELS.includes(level)) continue;
        const values = byPeriod.get(b.period) ?? {};
        values[level] = (values[level] ?? 0) + b.count;
        byPeriod.set(b.period, values);
      }
      const points = fillGaps(
        [...byPeriod.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([period, values]) => ({ period, values })),
        bucket,
      );
      return { overview: summarize(current.buckets, previous.buckets), points, latestErrors: latest.items };
    },
    JSON.stringify(range),
  );

  // Filters that reproduce this view on the detail pages.
  const periodFilters: Filters = { ...EMPTY_FILTERS, from: isoToLocal(range.from) };
  const errorFilters: Filters = { ...periodFilters, levels: [...ERROR_LEVELS] };
  const recent = loadRecent().slice(0, 3);
  const o = data?.overview;

  return (
    <section aria-labelledby="home-title" className="home">
      <div className="page-head">
        <h1 id="home-title">Inicio</h1>
        <div className="segmented" role="radiogroup" aria-label="Período">
          {(Object.keys(WINDOWS) as OverviewWindow[]).map((w) => (
            <label key={w} className={w === span ? "selected" : ""}>
              <input type="radio" name="period" checked={w === span} onChange={() => setSpan(w)} />
              {WINDOWS[w].label}
            </label>
          ))}
        </div>
        <div className="page-actions">
          <button className="button" onClick={() => setRefresh((n) => n + 1)} disabled={loading}>
            Actualizar
          </button>
          <button
            className="button primary"
            onClick={() => onAnalyzePeriod({ ...periodFilters, levels: ["critical", "error", "warn"] })}
            disabled={!o?.total}
          >
            Analizar este período con IA
          </button>
        </div>
      </div>

      <ErrorBanner error={error} title="No se pudo cargar el resumen" />

      {o && o.total === 0 && (
        <div className="banner banner-info">
          <strong>No hay logs en este período.</strong>
          <p>
            Elegí un período más largo o <a href="#/ingest">ingresá logs</a>.
          </p>
        </div>
      )}

      {o && o.total > 0 && (
        <div aria-busy={loading} className="home-body">
          <div className="kpi-grid">
            <Kpi
              title="Logs"
              value={formatNumber(o.total)}
              delta={<Delta value={change(o.total, o.previousTotal)} />}
              onOpen={() => onOpenLogs(periodFilters)}
              linkLabel="Ver logs"
            />
            <Kpi
              title="Errores y críticos"
              value={formatNumber(o.errors)}
              detail={o.byLevel.critical ? `${formatNumber(o.byLevel.critical)} críticos` : "ningún crítico"}
              delta={<Delta value={change(o.errors, o.previousErrors)} goodWhenDown />}
              onOpen={() => onOpenLogs(errorFilters)}
              linkLabel="Ver errores"
              tone={o.errors ? "danger" : undefined}
            />
            <Kpi
              title="Tasa de error"
              value={formatPercent(o.errorRate)}
              detail="de los logs son error o crítico"
              delta={
                <Delta
                  value={o.previousTotal ? o.errorRate - o.previousErrorRate : null}
                  goodWhenDown
                  label={`${Math.abs((o.errorRate - o.previousErrorRate) * 100).toFixed(1)} pp`}
                />
              }
              onOpen={() => onOpenStats(periodFilters)}
              linkLabel="Ver estadísticas"
            />
            <Kpi
              title="Servicios con errores"
              value={`${o.servicesWithErrors} de ${o.services.length}`}
              detail={o.services[0]?.errors ? `más afectado: ${o.services[0].service}` : "todos sin errores"}
              delta={<span className="delta muted">en el período</span>}
              onOpen={() => onOpenStats(errorFilters)}
              linkLabel="Ver por servicio"
            />
          </div>

          <div className="home-grid">
            <div className="card">
              <div className="card-head">
                <h2>Errores y advertencias {bucket === "hour" ? "por hora" : "por día"}</h2>
                <button className="link-button small" onClick={() => onOpenStats(periodFilters)}>
                  Ver en Estadísticas →
                </button>
              </div>
              {data.points.length ? (
                <TimeSeriesChart
                  points={data.points}
                  series={CHART_LEVELS.filter((l) => data.points.some((p) => p.values[l]))}
                  tone={(s) => `level-${s}`}
                  formatPeriod={(iso) => formatPeriod(iso, bucket)}
                  ariaLabel={`Errores y advertencias ${bucket === "hour" ? "por hora" : "por día"}`}
                />
              ) : (
                <p className="muted">Sin errores ni advertencias en este período.</p>
              )}
            </div>

            <div className="card">
              <div className="card-head">
                <h2>Servicios con más errores</h2>
                <button className="link-button small" onClick={() => onOpenStats(errorFilters)}>
                  Ver todos →
                </button>
              </div>
              {o.servicesWithErrors ? (
                <BarList
                  ariaLabel="Errores por servicio"
                  data={o.services
                    .filter((s) => s.errors > 0)
                    .slice(0, TOP_SERVICES)
                    .map((s) => ({
                      label: s.service,
                      value: s.errors,
                      detail: `(${formatPercent(s.errors / s.total, 0)})`,
                      tone: "level-error",
                    }))}
                  onSelect={(d) => onOpenLogs({ ...errorFilters, services: d.label })}
                />
              ) : (
                <p className="muted">Ningún servicio tuvo errores en este período.</p>
              )}
            </div>

            <div className="card">
              <div className="card-head">
                <h2>Últimos errores</h2>
                <button className="link-button small" onClick={() => onOpenLogs(errorFilters)}>
                  Ver todos →
                </button>
              </div>
              {data.latestErrors.length ? (
                <ul className="latest-errors">
                  {data.latestErrors.map((log) => (
                    <li key={log.id}>
                      <button type="button" className="row-link" onClick={() => setOpenLog(log)}>
                        <span className="latest-head">
                          <LevelBadge level={levelFromSeverity(log.event.severity)} />
                          <span className="muted small">
                            {log.service.name} · {formatDateTime(log["@timestamp"])}
                          </span>
                        </span>
                        <span className="latest-message">{log.message}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Sin errores en este período.</p>
              )}
            </div>

            <div className="card">
              <div className="card-head">
                <h2>Últimos análisis</h2>
                <a className="small" href="#/analysis">
                  Ir a Análisis →
                </a>
              </div>
              {recent.length ? (
                <ul className="latest-errors">
                  {recent.map((r) => (
                    <li key={r.id}>
                      <a className="row-link" href={`#/analysis/${r.id}`}>
                        <span className="latest-head">
                          <StatusBadge status={r.status} />
                          <span className="muted small">
                            {formatDateTime(r.createdAt)} · {formatNumber(r.logCount)} logs
                          </span>
                        </span>
                        <span className="latest-message">{r.summary ?? "Sin resumen"}</span>
                      </a>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  Todavía no corriste análisis desde este navegador. Probá con “Analizar este período con IA”.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {openLog && (
        <LogDetail
          log={openLog}
          onClose={() => setOpenLog(null)}
          onAnalyze={(id) => onAnalyzeIds([id])}
          onFilterTrace={(traceId) => onOpenLogs({ ...EMPTY_FILTERS, traceId })}
        />
      )}
    </section>
  );
}

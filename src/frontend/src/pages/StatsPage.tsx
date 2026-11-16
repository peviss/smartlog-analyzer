import { useMemo, useState } from "react";

import { getStats, type GroupBy, type StatsBucket } from "../api/client";
import { BarList, fillGaps, TimeSeriesChart, type TimeSeriesPoint } from "../components/Charts";
import { ErrorBanner } from "../components/ErrorBanner";
import { FilterBar } from "../components/FilterBar";
import { type Filters, LEVELS, toQuery } from "../lib/filters";
import { formatDateTime, formatNumber, formatPeriod, formatShortDateTime } from "../lib/format";
import { useApi } from "../lib/useApi";

const DIMENSIONS: { key: Exclude<GroupBy, "hour" | "day">; label: string }[] = [
  { key: "service.name", label: "Servicio" },
  { key: "log.level", label: "Nivel" },
  { key: "service.environment", label: "Entorno" },
  { key: "host.name", label: "Host" },
];
const PERIODS: { key: "hour" | "day" | ""; label: string }[] = [
  { key: "", label: "Sin agrupar por tiempo" },
  { key: "hour", label: "Por hora" },
  { key: "day", label: "Por día" },
];

interface Props {
  filters: Filters;
  onFiltersChange: (filters: Filters) => void;
}

function bucketLabel(bucket: StatsBucket, dims: GroupBy[]): string {
  return dims
    .filter((d) => d !== "hour" && d !== "day")
    .map((d) => bucket[d as keyof StatsBucket] ?? "(vacío)")
    .join(" · ");
}

export function StatsPage({ filters, onFiltersChange }: Props) {
  const [dims, setDims] = useState<GroupBy[]>(["service.name", "log.level"]);
  const [period, setPeriod] = useState<"hour" | "day" | "">("hour");
  const groupBy = useMemo<GroupBy[]>(() => {
    const g: GroupBy[] = [...dims];
    if (period) g.push(period);
    return g.length ? g : ["log.level"];
  }, [dims, period]);
  const query = useMemo(() => ({ ...toQuery(filters), group_by: groupBy }), [filters, groupBy]);
  const { data, error, loading } = useApi((signal) => getStats(query, signal), JSON.stringify(query));

  const toggleDim = (d: GroupBy) => setDims((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d]));
  const buckets = data?.buckets ?? [];

  // Time series: stack by level if grouped by level, otherwise by the first dimension.
  const seriesDim = dims.includes("log.level") ? "log.level" : dims[0];
  const timeSeries = useMemo(() => {
    if (!period) return null;
    const byPeriod = new Map<string, Record<string, number>>();
    const names = new Set<string>();
    for (const b of buckets) {
      if (!b.period) continue;
      const name = seriesDim ? String(b[seriesDim as keyof StatsBucket] ?? "(vacío)") : "logs";
      names.add(name);
      const values = byPeriod.get(b.period) ?? {};
      values[name] = (values[name] ?? 0) + b.count;
      byPeriod.set(b.period, values);
    }
    const points: TimeSeriesPoint[] = fillGaps(
      [...byPeriod.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([p, values]) => ({ period: p, values })),
      period,
    );
    const series =
      seriesDim === "log.level" ? LEVELS.filter((l) => names.has(l)) : [...names].sort();
    return { points, series };
  }, [buckets, period, seriesDim]);

  const ranked = useMemo(() => {
    const totals = new Map<string, number>();
    for (const b of buckets) {
      const label = bucketLabel(b, groupBy) || "Total";
      totals.set(label, (totals.get(label) ?? 0) + b.count);
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 25)
      .map(([label, value]) => ({ label, value }));
  }, [buckets, groupBy]);

  const toneFor = (s: string) => (seriesDim === "log.level" ? `level-${s}` : `series-${[...(timeSeries?.series ?? [])].indexOf(s) % 6}`);

  return (
    <section aria-labelledby="stats-title">
      <div className="page-head">
        <h1 id="stats-title">Estadísticas</h1>
        {data && <p className="muted">{formatNumber(data.total)} logs</p>}
      </div>

      <FilterBar value={filters} onApply={onFiltersChange}>
        <fieldset className="inline-field">
          <legend>Agrupar por</legend>
          <div className="chips">
            {DIMENSIONS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                className={`chip${dims.includes(key) ? " selected" : ""}`}
                aria-pressed={dims.includes(key)}
                onClick={() => toggleDim(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>
        <label className="inline-field">
          <span className="sr-only">Período</span>
          <select value={period} onChange={(e) => setPeriod(e.target.value as typeof period)}>
            {PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </FilterBar>

      <ErrorBanner error={error} title="No se pudieron cargar las estadísticas" />

      {timeSeries && timeSeries.points.length > 0 && (
        <div className="card">
          <h2>Logs {period === "hour" ? "por hora" : "por día"}</h2>
          <TimeSeriesChart
            points={timeSeries.points}
            series={timeSeries.series}
            tone={toneFor}
            formatPeriod={(iso) => formatPeriod(iso, period || "hour")}
            ariaLabel={`Cantidad de logs ${period === "hour" ? "por hora" : "por día"}`}
          />
        </div>
      )}

      {ranked.length > 0 && dims.length > 0 && (
        <div className="card">
          <h2>Ranking {ranked.length === 25 ? "(25 grupos con más logs)" : ""}</h2>
          <BarList
            ariaLabel="Cantidad de logs por grupo"
            data={ranked.map((r) => ({
              ...r,
              tone: dims.length === 1 && dims[0] === "log.level" ? `level-${r.label}` : undefined,
            }))}
          />
        </div>
      )}

      <div className="card table-card" aria-busy={loading}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {period && <th>Período</th>}
                {dims.map((d) => (
                  <th key={d}>{d}</th>
                ))}
                <th className="num">Logs</th>
                <th>Primero</th>
                <th>Último</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((b, i) => (
                <tr key={i}>
                  {period && <td className="nowrap mono small">{b.period ? formatShortDateTime(b.period) : "—"}</td>}
                  {dims.map((d) => (
                    <td key={d}>{String(b[d as keyof StatsBucket] ?? "—")}</td>
                  ))}
                  <td className="num">{formatNumber(b.count)}</td>
                  <td className="nowrap small">{formatDateTime(b.first_seen)}</td>
                  <td className="nowrap small">{formatDateTime(b.last_seen)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !error && buckets.length === 0 && <p className="empty">No hay datos para estos filtros.</p>}
      </div>
    </section>
  );
}

import { useEffect, useMemo, useState } from "react";

import { listLogs, type LogRead } from "../api/client";
import { LevelBadge, Tag } from "../components/Badges";
import { ErrorBanner } from "../components/ErrorBanner";
import { FilterBar } from "../components/FilterBar";
import { LogDetail } from "../components/LogDetail";
import { type Filters, toQuery } from "../lib/filters";
import { formatDateTime, formatNumber, levelFromSeverity } from "../lib/format";
import { useApi } from "../lib/useApi";

const PAGE_SIZE = 50;

interface Props {
  filters: Filters;
  onFiltersChange: (filters: Filters) => void;
  onAnalyzeIds: (ids: number[]) => void;
  onAnalyzeFilters: () => void;
}

export function LogsPage({ filters, onFiltersChange, onAnalyzeIds, onAnalyzeFilters }: Props) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<LogRead | null>(null);
  const query = useMemo(() => ({ ...toQuery(filters), limit: PAGE_SIZE, offset }), [filters, offset]);
  const { data, error, loading, reload } = useApi((signal) => listLogs(query, signal), JSON.stringify(query));

  // A new filter set starts from the first page with nothing selected.
  useEffect(() => {
    setOffset(0);
    setSelected(new Set());
  }, [filters]);

  const items = data?.items ?? [];
  const page = data?.page;
  const allOnPageSelected = items.length > 0 && items.every((l) => selected.has(l.id));

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const togglePage = () =>
    setSelected((s) => {
      const next = new Set(s);
      for (const l of items) {
        if (allOnPageSelected) next.delete(l.id);
        else next.add(l.id);
      }
      return next;
    });

  return (
    <section aria-labelledby="logs-title">
      <div className="page-head">
        <h1 id="logs-title">Logs</h1>
        <div className="page-actions">
          <button className="button" onClick={reload} disabled={loading}>
            Actualizar
          </button>
          <button className="button" onClick={onAnalyzeFilters} disabled={!page?.total}>
            Analizar con estos filtros
          </button>
          <button
            className="button primary"
            onClick={() => onAnalyzeIds([...selected])}
            disabled={selected.size === 0}
          >
            Analizar seleccionados{selected.size ? ` (${selected.size})` : ""}
          </button>
        </div>
      </div>

      <FilterBar value={filters} onApply={onFiltersChange} />
      <ErrorBanner error={error} title="No se pudieron cargar los logs" />

      <div className="card table-card" aria-busy={loading}>
        <div className="table-scroll">
          <table className="log-table">
            <thead>
              <tr>
                <th className="check">
                  <input
                    type="checkbox"
                    aria-label="Seleccionar los logs de esta página"
                    checked={allOnPageSelected}
                    onChange={togglePage}
                  />
                </th>
                <th>@timestamp</th>
                <th>Nivel</th>
                <th>Servicio</th>
                <th className="message-col">Mensaje</th>
              </tr>
            </thead>
            <tbody>
              {items.map((log) => (
                <tr key={log.id} className={selected.has(log.id) ? "selected" : undefined}>
                  <td className="check">
                    <input
                      type="checkbox"
                      aria-label={`Seleccionar log ${log.id}`}
                      checked={selected.has(log.id)}
                      onChange={() => toggle(log.id)}
                    />
                  </td>
                  <td className="nowrap mono small">{formatDateTime(log["@timestamp"])}</td>
                  <td>
                    <LevelBadge level={levelFromSeverity(log.event.severity)} />
                  </td>
                  <td className="nowrap">{log.service.name}</td>
                  <td className="message-col">
                    <button className="row-link" onClick={() => setOpen(log)}>
                      {log.message}
                    </button>
                    {log.tags?.map((t) => <Tag key={t} tag={t} />)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !error && items.length === 0 && (
          <p className="empty">No hay logs que coincidan con los filtros.</p>
        )}
        {page && page.total > 0 && (
          <footer className="pager">
            <span className="muted small">
              {formatNumber(page.offset + 1)}–{formatNumber(page.offset + items.length)} de{" "}
              {formatNumber(page.total)}
            </span>
            <button
              className="button"
              disabled={page.offset === 0 || loading}
              onClick={() => setOffset(Math.max(0, page.offset - PAGE_SIZE))}
            >
              Anterior
            </button>
            <button
              className="button"
              disabled={!page.has_more || loading}
              onClick={() => setOffset(page.offset + PAGE_SIZE)}
            >
              Siguiente
            </button>
          </footer>
        )}
      </div>

      {open && (
        <LogDetail
          log={open}
          onClose={() => setOpen(null)}
          onAnalyze={(id) => onAnalyzeIds([id])}
          onFilterTrace={(traceId) => {
            setOpen(null);
            onFiltersChange({ ...filters, traceId });
          }}
        />
      )}
    </section>
  );
}

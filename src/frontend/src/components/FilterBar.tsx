import { type FormEvent, useEffect, useId, useState } from "react";

import type { Level } from "../api/client";
import { activeFilterCount, EMPTY_FILTERS, type Filters, LEVELS } from "../lib/filters";

interface Props {
  value: Filters;
  onApply: (filters: Filters) => void;
  /** Extra controls rendered next to the action buttons (e.g. group_by on the stats page). */
  children?: React.ReactNode;
}

/** Edits a draft; the parent only sees the filters when they are applied. */
export function FilterBar({ value, onApply, children }: Props) {
  const [draft, setDraft] = useState(value);
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  useEffect(() => setDraft(value), [value]);

  const set = <K extends keyof Filters>(key: K, v: Filters[K]) => setDraft((d) => ({ ...d, [key]: v }));
  const toggleLevel = (level: Level) =>
    set("levels", draft.levels.includes(level) ? draft.levels.filter((l) => l !== level) : [...draft.levels, level]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onApply(draft);
  };
  const active = activeFilterCount(value);

  return (
    <form className="filter-bar card" onSubmit={submit} aria-label="Filtros">
      <div className="filter-row">
        <label className="field grow">
          <span>Buscar en el mensaje</span>
          <input
            type="search"
            value={draft.q}
            onChange={(e) => set("q", e.target.value)}
            placeholder="connection refused, timeout…"
          />
        </label>
        <label className="field">
          <span>service.name</span>
          <input
            value={draft.services}
            onChange={(e) => set("services", e.target.value)}
            placeholder="payments-api, orders"
          />
        </label>
        <fieldset className="field">
          <legend>log.level</legend>
          <div className="chips">
            {LEVELS.map((level) => (
              <button
                key={level}
                type="button"
                className={`chip level-${level}${draft.levels.includes(level) ? " selected" : ""}`}
                aria-pressed={draft.levels.includes(level)}
                onClick={() => toggleLevel(level)}
              >
                {level}
              </button>
            ))}
          </div>
        </fieldset>
      </div>

      {expanded && (
        <div className="filter-row" id={`${id}-more`}>
          <label className="field">
            <span>Desde</span>
            <input type="datetime-local" value={draft.from} onChange={(e) => set("from", e.target.value)} />
          </label>
          <label className="field">
            <span>Hasta</span>
            <input type="datetime-local" value={draft.to} onChange={(e) => set("to", e.target.value)} />
          </label>
          <label className="field">
            <span>service.environment</span>
            <input value={draft.environment} onChange={(e) => set("environment", e.target.value)} />
          </label>
          <label className="field">
            <span>host.name</span>
            <input value={draft.host} onChange={(e) => set("host", e.target.value)} />
          </label>
          <label className="field">
            <span>event.dataset</span>
            <input value={draft.dataset} onChange={(e) => set("dataset", e.target.value)} />
          </label>
          <label className="field">
            <span>trace.id</span>
            <input value={draft.traceId} onChange={(e) => set("traceId", e.target.value)} />
          </label>
        </div>
      )}

      <div className="filter-actions">
        <button
          type="button"
          className="link-button"
          aria-expanded={expanded}
          aria-controls={`${id}-more`}
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? "Menos filtros" : "Más filtros (fechas, entorno, host, traza)"}
        </button>
        {children}
        <span className="spacer" />
        {active > 0 && (
          <button type="button" className="button ghost" onClick={() => onApply(EMPTY_FILTERS)}>
            Limpiar ({active})
          </button>
        )}
        <button type="submit" className="button primary">
          Aplicar
        </button>
      </div>
    </form>
  );
}

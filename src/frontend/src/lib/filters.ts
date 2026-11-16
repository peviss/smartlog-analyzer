import type { FilterQuery, Level } from "../api/client";

export const LEVELS: Level[] = ["critical", "error", "warn", "info", "debug"];

/** Form state of the filter bar. Kept as strings, converted to the API query on submit. */
export interface Filters {
  /** Comma-separated service.name values. */
  services: string;
  levels: Level[];
  environment: string;
  host: string;
  dataset: string;
  traceId: string;
  /** `datetime-local` values, in the browser's time zone. */
  from: string;
  to: string;
  q: string;
}

export const EMPTY_FILTERS: Filters = {
  services: "",
  levels: [],
  environment: "",
  host: "",
  dataset: "",
  traceId: "",
  from: "",
  to: "",
  q: "",
};

/** `datetime-local` (local time, no zone) -> ISO 8601 in UTC, or undefined if empty/invalid. */
export function localToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/** ISO 8601 -> `datetime-local` value in the browser's time zone. */
export function isoToLocal(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** Filters -> API query with ECS parameter names. Empty values are left out. */
export function toQuery(f: Filters): FilterQuery {
  const query: FilterQuery = {};
  const services = f.services
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
  if (services.length) query["service.name"] = services;
  if (f.levels.length) query["log.level"] = f.levels;
  if (f.environment.trim()) query["service.environment"] = f.environment.trim();
  if (f.host.trim()) query["host.name"] = f.host.trim();
  if (f.dataset.trim()) query["event.dataset"] = f.dataset.trim();
  if (f.traceId.trim()) query["trace.id"] = f.traceId.trim();
  const from = localToIso(f.from);
  const to = localToIso(f.to);
  if (from) query.from = from;
  if (to) query.to = to;
  if (f.q.trim()) query.q = f.q.trim();
  return query;
}

export function activeFilterCount(f: Filters): number {
  return Object.keys(toQuery(f)).length;
}

/** Human summary of the active filters, e.g. "service.name: payments · log.level: error". */
export function describeFilters(f: Filters): string {
  const parts = Object.entries(toQuery(f)).map(([key, value]) => {
    const shown = Array.isArray(value) ? value.join(", ") : String(value);
    return `${key}: ${key === "from" || key === "to" ? new Date(shown).toLocaleString() : shown}`;
  });
  return parts.length ? parts.join(" · ") : "Sin filtros: todos los logs";
}

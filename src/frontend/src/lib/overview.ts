/**
 * Numbers for the home page, computed from /api/logs/stats buckets.
 * Pure functions: the page fetches, this module summarizes (and is unit tested).
 */
import type { Level, StatsBucket } from "../api/client";

export type OverviewWindow = "24h" | "7d" | "30d";

export const WINDOWS: Record<OverviewWindow, { label: string; ms: number; bucket: "hour" | "day" }> = {
  "24h": { label: "Últimas 24 h", ms: 24 * 3600_000, bucket: "hour" },
  "7d": { label: "Últimos 7 días", ms: 7 * 24 * 3600_000, bucket: "hour" },
  "30d": { label: "Últimos 30 días", ms: 30 * 24 * 3600_000, bucket: "day" },
};

/** Levels that count as errors in the summary. */
export const ERROR_LEVELS: Level[] = ["critical", "error"];

export interface WindowRange {
  from: string;
  to: string;
  /** Start of the previous period of the same length, for comparisons. */
  previousFrom: string;
}

export function windowRange(window: OverviewWindow, now: Date = new Date()): WindowRange {
  const { ms } = WINDOWS[window];
  return {
    from: new Date(now.getTime() - ms).toISOString(),
    to: now.toISOString(),
    previousFrom: new Date(now.getTime() - 2 * ms).toISOString(),
  };
}

export interface ServiceSummary {
  service: string;
  total: number;
  errors: number;
}

export interface Overview {
  total: number;
  errors: number;
  /** errors / total, 0 when there are no logs. */
  errorRate: number;
  byLevel: Partial<Record<Level, number>>;
  /** Services sorted by errors, then by volume. */
  services: ServiceSummary[];
  servicesWithErrors: number;
  previousTotal: number;
  previousErrors: number;
  previousErrorRate: number;
}

const isError = (level: Level | null | undefined) => !!level && ERROR_LEVELS.includes(level);

/**
 * @param current buckets grouped by service.name and log.level for the window
 * @param previous buckets grouped by log.level for the previous window
 */
export function summarize(current: StatsBucket[], previous: StatsBucket[]): Overview {
  const byLevel: Partial<Record<Level, number>> = {};
  const services = new Map<string, ServiceSummary>();
  let total = 0;
  let errors = 0;
  for (const bucket of current) {
    const level = bucket["log.level"];
    total += bucket.count;
    if (level) byLevel[level] = (byLevel[level] ?? 0) + bucket.count;
    if (isError(level)) errors += bucket.count;
    const name = bucket["service.name"];
    if (name) {
      const entry = services.get(name) ?? { service: name, total: 0, errors: 0 };
      entry.total += bucket.count;
      if (isError(level)) entry.errors += bucket.count;
      services.set(name, entry);
    }
  }
  const previousTotal = previous.reduce((sum, b) => sum + b.count, 0);
  const previousErrors = previous.filter((b) => isError(b["log.level"])).reduce((sum, b) => sum + b.count, 0);
  const sorted = [...services.values()].sort((a, b) => b.errors - a.errors || b.total - a.total);
  return {
    total,
    errors,
    errorRate: total ? errors / total : 0,
    byLevel,
    services: sorted,
    servicesWithErrors: sorted.filter((s) => s.errors > 0).length,
    previousTotal,
    previousErrors,
    previousErrorRate: previousTotal ? previousErrors / previousTotal : 0,
  };
}

/** Relative change vs the previous period; null when there is nothing to compare with. */
export function change(current: number, previous: number): number | null {
  if (previous === 0) return current === 0 ? 0 : null;
  return (current - previous) / previous;
}

export function formatPercent(ratio: number, digits = 1): string {
  return `${(ratio * 100).toFixed(digits).replace(/\.0$/, "")} %`;
}

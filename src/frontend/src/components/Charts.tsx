import { formatNumber } from "../lib/format";

export interface BarDatum {
  label: string;
  value: number;
  /** CSS class suffix for the bar color, e.g. "level-error". */
  tone?: string;
  /** Extra text after the value, e.g. "de 400". */
  detail?: string;
}

interface BarListProps {
  data: BarDatum[];
  ariaLabel: string;
  /** Makes each label a link to its details. */
  onSelect?: (datum: BarDatum) => void;
}

/** Ranked horizontal bars. Values are printed, so the chart is readable without hovering. */
export function BarList({ data, ariaLabel, onSelect }: BarListProps) {
  const max = Math.max(1, ...data.map((d) => d.value));
  return (
    <ul className="bar-list" aria-label={ariaLabel}>
      {data.map((d) => (
        <li key={d.label}>
          {onSelect ? (
            <button type="button" className="bar-label link-button" title={`Ver ${d.label}`} onClick={() => onSelect(d)}>
              {d.label}
            </button>
          ) : (
            <span className="bar-label" title={d.label}>
              {d.label}
            </span>
          )}
          <span className="bar-track">
            <span className={`bar-fill ${d.tone ?? ""}`} style={{ width: `${(d.value / max) * 100}%` }} />
          </span>
          <span className="bar-value">
            {formatNumber(d.value)}
            {d.detail && <span className="muted"> {d.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export interface TimeSeriesPoint {
  /** ISO start of the bucket. */
  period: string;
  /** Counts per series (e.g. per level) inside the bucket. */
  values: Record<string, number>;
}

interface TimeSeriesProps {
  points: TimeSeriesPoint[];
  series: string[];
  /** CSS class per series for the fill color. */
  tone: (series: string) => string;
  formatPeriod: (iso: string) => string;
  ariaLabel: string;
}

/**
 * Stacked columns over time, drawn in SVG; the table next to it carries the exact numbers.
 * `points` must be continuous (see fillGaps), so the x axis is proportional to time.
 */
export function TimeSeriesChart({ points, series, tone, formatPeriod, ariaLabel }: TimeSeriesProps) {
  const width = 720;
  const height = 220;
  const pad = { top: 12, right: 8, bottom: 28, left: 44 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const totals = points.map((p) => series.reduce((sum, s) => sum + (p.values[s] ?? 0), 0));
  const max = Math.max(1, ...totals);
  const slot = innerW / Math.max(1, points.length);
  const barW = Math.max(2, Math.min(36, slot * 0.7));
  const labelEvery = Math.max(1, Math.ceil(points.length / 8));
  const ticks = [0, 0.5, 1].map((f) => Math.round(max * f));

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
        {ticks.map((t) => {
          const y = pad.top + innerH - (t / max) * innerH;
          return (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y} y2={y} className="grid" />
              <text x={pad.left - 6} y={y + 4} textAnchor="end" className="axis">
                {formatNumber(t)}
              </text>
            </g>
          );
        })}
        {points.map((p, i) => {
          const x = pad.left + i * slot + (slot - barW) / 2;
          let y = pad.top + innerH;
          return (
            <g key={p.period}>
              <title>
                {`${formatPeriod(p.period)}: ${formatNumber(totals[i])}` +
                  series
                    .filter((s) => p.values[s])
                    .map((s) => `\n${s}: ${formatNumber(p.values[s])}`)
                    .join("")}
              </title>
              {series.map((s) => {
                const v = p.values[s] ?? 0;
                if (!v) return null;
                const h = (v / max) * innerH;
                y -= h;
                return <rect key={s} x={x} y={y} width={barW} height={h} className={`bar ${tone(s)}`} />;
              })}
              {i % labelEvery === 0 && (
                <text x={x + barW / 2} y={height - 8} textAnchor="middle" className="axis">
                  {formatPeriod(p.period)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {series.length > 1 && (
        <figcaption className="legend">
          {series.map((s) => (
            <span key={s}>
              <i className={`swatch ${tone(s)}`} /> {s}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}

const STEP_MS = { hour: 3_600_000, day: 86_400_000 } as const;

/** Add empty buckets for periods without logs, so gaps in time show as gaps in the chart. */
export function fillGaps(points: TimeSeriesPoint[], period: "hour" | "day"): TimeSeriesPoint[] {
  if (points.length < 2) return points;
  const step = STEP_MS[period];
  const byTime = new Map(points.map((p) => [new Date(p.period).getTime(), p]));
  const times = [...byTime.keys()].sort((a, b) => a - b);
  const filled: TimeSeriesPoint[] = [];
  // Cap the number of buckets so a sparse, very long range cannot freeze the page.
  for (let t = times[0]; t <= times[times.length - 1] && filled.length < 2000; t += step) {
    filled.push(byTime.get(t) ?? { period: new Date(t).toISOString(), values: {} });
  }
  return filled;
}

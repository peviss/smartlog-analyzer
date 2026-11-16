import type { Level } from "../api/client";

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" });
const shortDateTime = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const hourLabel = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit" });
const dayLabel = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });
const number = new Intl.NumberFormat();

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : dateTime.format(date);
}

export function formatShortDateTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : shortDateTime.format(date);
}

/** Compact axis label for a time bucket. */
export function formatPeriod(iso: string, period: "hour" | "day"): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return (period === "hour" ? hourLabel : dayLabel).format(date);
}

export function formatNumber(n: number): string {
  return number.format(n);
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Normalized level from ECS event.severity (syslog codes, as SmartLog derives it). */
export function levelFromSeverity(severity: number): Level {
  if (severity <= 2) return "critical";
  if (severity === 3) return "error";
  if (severity === 4) return "warn";
  if (severity <= 6) return "info";
  return "debug";
}

/** Tags SmartLog adds when it had to assume something (Elastic-style `_` prefix). */
export function isProcessingTag(tag: string): boolean {
  return tag.startsWith("_");
}

export const PROCESSING_TAG_HELP: Record<string, string> = {
  _level_unrecognized: "El nivel original no es una grafía conocida; se contó como info.",
  _timestamp_missing: "El log llegó sin @timestamp; se usó la hora de ingesta.",
  _timestamp_unparsed: "La fecha no se pudo leer; quedó en el label original_timestamp.",
  _level_missing: "El registro no traía nivel; se usó el nivel por defecto.",
  _message_missing: "El registro no traía mensaje; el mensaje es el registro completo.",
  _truncated: "Un campo superaba el tamaño máximo y se recortó; el original está en event.original.",
  _fields_dropped: "Había demasiados campos extra; el registro completo está en event.original.",
};

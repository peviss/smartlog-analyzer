import type { Level } from "../api/client";
import { isProcessingTag, PROCESSING_TAG_HELP } from "../lib/format";

export function LevelBadge({ level }: { level: Level }) {
  return <span className={`badge level-${level}`}>{level}</span>;
}

type Severity = "low" | "medium" | "high" | "critical";

const SEVERITY_LABEL: Record<Severity, string> = {
  low: "baja",
  medium: "media",
  high: "alta",
  critical: "crítica",
};

export function SeverityBadge({ value, prefix }: { value: Severity; prefix?: string }) {
  return (
    <span className={`badge sev-${value}`}>
      {prefix ? `${prefix} ` : ""}
      {SEVERITY_LABEL[value]}
    </span>
  );
}

export function Tag({ tag }: { tag: string }) {
  const processing = isProcessingTag(tag);
  return (
    <span className={processing ? "tag tag-processing" : "tag"} title={PROCESSING_TAG_HELP[tag]}>
      {tag}
    </span>
  );
}

export function StatusBadge({ status }: { status: "pending" | "completed" | "failed" }) {
  const label = { pending: "en curso", completed: "completado", failed: "falló" }[status];
  return <span className={`badge status-${status}`}>{label}</span>;
}

import { useEffect, useRef } from "react";

import type { LogRead } from "../api/client";
import { formatDateTime, levelFromSeverity } from "../lib/format";
import { LevelBadge, Tag } from "./Badges";

interface Props {
  log: LogRead;
  onClose: () => void;
  onFilterTrace: (traceId: string) => void;
  onAnalyze: (id: number) => void;
}

/** Side panel with the full ECS document of one log. */
export function LogDetail({ log, onClose, onFilterTrace, onAnalyze }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const traceId = log.trace?.id;
  const rows: [string, React.ReactNode][] = [
    ["@timestamp", formatDateTime(log["@timestamp"])],
    ["event.ingested", formatDateTime(log.event.ingested)],
    ["log.level (original)", <code key="l">{log.log.level}</code>],
    ["event.severity", log.event.severity],
    ["service", [log.service.name, log.service.version, log.service.environment].filter(Boolean).join(" · ")],
    ["host.name", log.host?.name],
    ["log.logger", log.log.logger],
    ["trace.id", traceId && <code key="t">{traceId}</code>],
    ["event.id", log.event.id && <code key="e">{log.event.id}</code>],
    ["event.dataset", log.event.dataset],
  ];

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="log-detail-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer-header">
          <div>
            <h2 id="log-detail-title">
              Log #{log.id} <LevelBadge level={levelFromSeverity(log.event.severity)} />
            </h2>
            <p className="muted">{log.service.name}</p>
          </div>
          <button ref={closeRef} className="icon-button" onClick={onClose} aria-label="Cerrar">
            ✕
          </button>
        </header>

        <p className="log-message">{log.message}</p>

        {log.error && (log.error.type || log.error.message || log.error.stack_trace) && (
          <section>
            <h3>Error</h3>
            {log.error.type && <p><code>{log.error.type}</code></p>}
            {log.error.message && <p>{log.error.message}</p>}
            {log.error.stack_trace && <pre className="code-block">{log.error.stack_trace}</pre>}
          </section>
        )}

        <dl className="kv">
          {rows
            .filter(([, v]) => v !== undefined && v !== null && v !== "")
            .map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
        </dl>

        {(log.tags?.length || log.labels) && (
          <div className="tag-row">
            {log.tags?.map((t) => <Tag key={t} tag={t} />)}
            {log.labels &&
              Object.entries(log.labels).map(([k, v]) => (
                <span key={k} className="tag label">
                  {k}={v}
                </span>
              ))}
          </div>
        )}

        <div className="drawer-actions">
          <button className="button primary" onClick={() => onAnalyze(log.id)}>
            Analizar este log
          </button>
          {traceId && (
            <button className="button" onClick={() => onFilterTrace(traceId)}>
              Ver toda la traza
            </button>
          )}
        </div>

        <details>
          <summary>Documento ECS completo</summary>
          <pre className="code-block">{JSON.stringify(log, null, 2)}</pre>
        </details>
      </aside>
    </div>
  );
}

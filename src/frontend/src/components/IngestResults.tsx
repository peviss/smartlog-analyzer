import type { LogBatchResult, LogImportResult } from "../api/client";
import { formatNumber } from "../lib/format";

const FORMAT_LABEL = { text: "texto plano", json: "JSON", csv: "CSV" } as const;
const MAX_SHOWN = 50;

/** Outcome of a batch or an import: what was stored, skipped and rejected (with lines). */
export function BatchResult({ result }: { result: LogBatchResult | LogImportResult }) {
  const imported = "format" in result ? result : null;
  const errors = result.errors ?? [];
  return (
    <div className={`banner ${result.failed ? "banner-warn" : "banner-ok"}`} role="status">
      <strong>
        {formatNumber(result.ingested)} guardados · {formatNumber(result.duplicates)} duplicados ·{" "}
        {formatNumber(result.failed)} rechazados
      </strong>
      {imported && (
        <p className="muted small">
          {formatNumber(imported.records)} registros leídos como {FORMAT_LABEL[imported.format]}.
        </p>
      )}
      {errors.length > 0 && (
        <ul className="error-details">
          {errors.slice(0, MAX_SHOWN).map((e) => (
            <li key={e.index}>
              {e.line ? `Línea ${e.line}` : `Log ${e.index}`}:{" "}
              {e.errors.map((d, i) => (
                <span key={i}>
                  {d.field && <code>{d.field}</code>} {d.message}
                  {i < e.errors.length - 1 ? "; " : ""}
                </span>
              ))}
            </li>
          ))}
          {errors.length > MAX_SHOWN && <li className="muted">…y {errors.length - MAX_SHOWN} más.</li>}
        </ul>
      )}
    </div>
  );
}

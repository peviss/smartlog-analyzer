import { type FormEvent, useState } from "react";

import { ingestBatch, ingestOne, type LogBatchResult, type LogRead } from "../api/client";
import { LevelBadge, Tag } from "../components/Badges";
import { ErrorBanner } from "../components/ErrorBanner";
import { ImportPanel } from "../components/ImportPanel";
import { BatchResult } from "../components/IngestResults";
import { LEVELS } from "../lib/filters";
import { levelFromSeverity } from "../lib/format";
import { parseIngestInput, SAMPLE_DOCUMENT } from "../lib/ingest";

type Outcome =
  | { kind: "single"; log: LogRead; created: boolean }
  | { kind: "batch"; result: LogBatchResult };

function SingleResult({ log, created }: { log: LogRead; created: boolean }) {
  return (
    <div className={`banner ${created ? "banner-ok" : "banner-info"}`} role="status">
      <strong>{created ? `Log #${log.id} guardado` : `Ya existía: log #${log.id}`}</strong>
      <p>
        <LevelBadge level={levelFromSeverity(log.event.severity)} /> {log.service.name} — {log.message}{" "}
        {log.tags?.map((t) => <Tag key={t} tag={t} />)}
      </p>
      {!created && <p className="muted small">El event.id ya estaba guardado para este servicio (reintento).</p>}
    </div>
  );
}

export function IngestPage() {
  const [service, setService] = useState("");
  const [level, setLevel] = useState("error");
  const [message, setMessage] = useState("");
  const [json, setJson] = useState(SAMPLE_DOCUMENT);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<unknown>(null);
  const parsed = parseIngestInput(json);

  const send = async (work: () => Promise<Outcome>) => {
    setBusy(true);
    setError(null);
    setOutcome(null);
    try {
      setOutcome(await work());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const sendForm = (event: FormEvent) => {
    event.preventDefault();
    void send(async () => ({
      kind: "single",
      ...(await ingestOne({
        "@timestamp": new Date().toISOString(),
        "service.name": service,
        "log.level": level,
        message,
      })),
    }));
  };

  const sendJson = (event: FormEvent) => {
    event.preventDefault();
    if (parsed.kind === "invalid") return;
    void send(async () =>
      parsed.kind === "single"
        ? { kind: "single", ...(await ingestOne(parsed.doc)) }
        : { kind: "batch", result: await ingestBatch(parsed.logs) },
    );
  };

  return (
    <section aria-labelledby="ingest-title">
      <div className="page-head">
        <h1 id="ingest-title">Ingesta</h1>
        <p className="muted">
          Para producción, enviá los logs desde un collector a <code>POST /api/logs/batch</code>.
        </p>
      </div>

      <ImportPanel />

      {outcome?.kind === "single" && <SingleResult log={outcome.log} created={outcome.created} />}
      {outcome?.kind === "batch" && <BatchResult result={outcome.result} />}
      <ErrorBanner error={error} title="El log no se guardó" />

      <div className="ingest-grid">
        <form className="card" onSubmit={sendForm}>
          <h2>Log rápido</h2>
          <label className="field">
            <span>service.name</span>
            <input required value={service} onChange={(e) => setService(e.target.value)} placeholder="payments-api" />
          </label>
          <label className="field">
            <span>log.level</span>
            <select value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>message</span>
            <textarea required rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <div className="form-actions">
            <span className="spacer" />
            <button className="button primary" type="submit" disabled={busy}>
              Enviar
            </button>
          </div>
        </form>

        <form className="card" onSubmit={sendJson}>
          <h2>Documento ECS (JSON)</h2>
          <p className="muted small">
            Un objeto se envía a <code>/api/logs</code>; una lista (o <code>{'{"logs": [...]}'}</code>) a{" "}
            <code>/api/logs/batch</code>, hasta 1000 logs.
          </p>
          <label className="field">
            <span className="sr-only">JSON</span>
            <textarea
              className="mono"
              rows={14}
              spellCheck={false}
              value={json}
              onChange={(e) => setJson(e.target.value)}
              aria-invalid={parsed.kind === "invalid"}
              aria-describedby="json-status"
            />
          </label>
          <div className="form-actions">
            <span id="json-status" className={`small ${parsed.kind === "invalid" ? "text-error" : "muted"}`}>
              {parsed.kind === "invalid"
                ? parsed.message
                : parsed.kind === "batch"
                  ? `Lote de ${parsed.logs.length} logs`
                  : "Un documento"}
            </span>
            <span className="spacer" />
            <button type="button" className="button ghost" onClick={() => setJson(SAMPLE_DOCUMENT)}>
              Ejemplo
            </button>
            <button className="button primary" type="submit" disabled={busy || parsed.kind === "invalid"}>
              Enviar
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

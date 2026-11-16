import { type ChangeEvent, type FormEvent, useId, useRef, useState } from "react";

import { type ImportFormat, importLogs, type LogImportResult } from "../api/client";
import { LEVELS } from "../lib/filters";
import {
  countLines,
  formatBytes,
  guessFormat,
  IMPORT_ACCEPT,
  IMPORT_SAMPLE,
  MAX_IMPORT_BYTES,
  MAX_IMPORT_RECORDS,
} from "../lib/importFormat";
import { ErrorBanner } from "./ErrorBanner";
import { BatchResult } from "./IngestResults";

const FORMATS: { value: ImportFormat | "auto"; label: string }[] = [
  { value: "auto", label: "Automático" },
  { value: "text", label: "Texto plano" },
  { value: "json", label: "JSON / NDJSON" },
  { value: "csv", label: "CSV" },
];

interface Source {
  name: string | null; // file name, or null when pasted
  content: string;
  bytes: number;
}

/** Raw import: a file (.log, .txt, .json, .ndjson, .csv) or pasted text. */
export function ImportPanel() {
  const [source, setSource] = useState<Source>({ name: null, content: "", bytes: 0 });
  const [format, setFormat] = useState<ImportFormat | "auto">("auto");
  const [serviceName, setServiceName] = useState("");
  const [logLevel, setLogLevel] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LogImportResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const id = useId();

  const effective = format === "auto" ? guessFormat(source.name, source.content) : format;
  const lines = countLines(source.content);
  const tooBig = source.bytes > MAX_IMPORT_BYTES;
  const problem = tooBig
    ? `El archivo pesa ${formatBytes(source.bytes)}; el máximo es ${formatBytes(MAX_IMPORT_BYTES)}.`
    : lines > MAX_IMPORT_RECORDS * 4 // stack traces span several lines: only block what is clearly too much
      ? `Tiene ${lines} líneas; el máximo es ${MAX_IMPORT_RECORDS} registros.`
      : null;

  const loadFile = async (file: File) => {
    setResult(null);
    setError(null);
    // Read even if too big so the size is shown, but never more than needed to show it.
    const content = file.size > MAX_IMPORT_BYTES ? "" : await file.text();
    setSource({ name: file.name, content, bytes: file.size });
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void loadFile(file);
  };

  const onPaste = (content: string) =>
    setSource({ name: null, content, bytes: new TextEncoder().encode(content).length });

  const clearFile = () => {
    setSource({ name: null, content: "", bytes: 0 });
    if (fileInput.current) fileInput.current.value = "";
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await importLogs(source.content, { format: effective, serviceName, logLevel }));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card import-panel" onSubmit={submit} aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`}>Importar logs crudos</h2>
      <p className="muted small">
        No hace falta que los logs estén en ECS: se parsean y se mapean a ECS. Texto plano (syslog, access logs de
        nginx/Apache, klog, logfmt, Python logging, logback, log4j…), JSON (OpenTelemetry, GELF, Serilog, Google
        Cloud, CloudWatch, Docker o cualquier otro esquema) o CSV con cualquier encabezado. Cada registro se valida
        por separado; los rechazados se informan con su número de línea.
      </p>

      <div
        className={`dropzone${dragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) void loadFile(file);
        }}
      >
        {source.name ? (
          <p>
            <strong>{source.name}</strong> · {formatBytes(source.bytes)}
            {!tooBig && ` · ${lines} líneas`}{" "}
            <button type="button" className="link-button" onClick={clearFile}>
              Quitar
            </button>
          </p>
        ) : (
          <p>
            Arrastrá un archivo acá o{" "}
            <button type="button" className="link-button" onClick={() => fileInput.current?.click()}>
              elegilo
            </button>{" "}
            (.log, .txt, .json, .ndjson, .csv · hasta {formatBytes(MAX_IMPORT_BYTES)})
          </p>
        )}
        <input
          ref={fileInput}
          type="file"
          accept={IMPORT_ACCEPT}
          onChange={onFile}
          className="sr-only"
          aria-label="Archivo de logs"
          tabIndex={-1}
        />
      </div>

      {!source.name && (
        <label className="field">
          <span>…o pegá el contenido</span>
          <textarea
            className="mono"
            rows={8}
            spellCheck={false}
            value={source.content}
            onChange={(e) => onPaste(e.target.value)}
            placeholder={IMPORT_SAMPLE}
          />
        </label>
      )}

      <div className="filter-row import-options">
        <label className="field">
          <span>Formato</span>
          <select value={format} onChange={(e) => setFormat(e.target.value as ImportFormat | "auto")}>
            {FORMATS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
                {f.value === "auto" && source.content ? ` (${FORMATS.find((x) => x.value === effective)?.label})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>service.name si falta</span>
          <input value={serviceName} onChange={(e) => setServiceName(e.target.value)} placeholder="payments-api" />
        </label>
        <label className="field">
          <span>log.level si falta</span>
          <select value={logLevel} onChange={(e) => setLogLevel(e.target.value)}>
            <option value="">info (por defecto)</option>
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="form-actions">
        {problem ? <span className="small text-error">{problem}</span> : null}
        <span className="spacer" />
        {!source.name && !source.content && (
          <button type="button" className="button ghost" onClick={() => onPaste(IMPORT_SAMPLE)}>
            Ejemplo
          </button>
        )}
        <button className="button primary" type="submit" disabled={busy || !source.content.trim() || !!problem}>
          {busy ? "Importando…" : "Importar"}
        </button>
      </div>

      {result && <BatchResult result={result} />}
      <ErrorBanner error={error} title="No se pudo importar" />
    </form>
  );
}

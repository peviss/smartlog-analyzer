/**
 * What the JSON box on the ingest page accepts:
 * - one ECS document (object)                 -> POST /api/logs
 * - an array of documents                     -> POST /api/logs/batch
 * - `{"logs": [...]}`, the batch body itself  -> POST /api/logs/batch
 */
export type IngestInput =
  | { kind: "single"; doc: Record<string, unknown> }
  | { kind: "batch"; logs: unknown[] }
  | { kind: "invalid"; message: string };

export const MAX_BATCH = 1000;

export function parseIngestInput(text: string): IngestInput {
  if (!text.trim()) return { kind: "invalid", message: "Pegá un documento JSON." };
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    return { kind: "invalid", message: `JSON inválido: ${(error as Error).message}` };
  }
  const isObject = (v: unknown): v is Record<string, unknown> =>
    typeof v === "object" && v !== null && !Array.isArray(v);

  let logs: unknown[] | undefined;
  if (Array.isArray(value)) logs = value;
  else if (isObject(value) && Array.isArray(value.logs) && Object.keys(value).length === 1) logs = value.logs;

  if (logs) {
    if (logs.length === 0) return { kind: "invalid", message: "La lista está vacía." };
    if (logs.length > MAX_BATCH) {
      return { kind: "invalid", message: `Un lote admite hasta ${MAX_BATCH} logs (hay ${logs.length}).` };
    }
    return { kind: "batch", logs };
  }
  if (isObject(value)) return { kind: "single", doc: value };
  return { kind: "invalid", message: "Se espera un objeto, una lista o {\"logs\": [...]}." };
}

export const SAMPLE_DOCUMENT = JSON.stringify(
  {
    "@timestamp": new Date().toISOString(),
    "log.level": "ERROR",
    message: "Could not acquire database connection after 5000ms",
    service: { name: "payments-api", version: "1.4.2", environment: "production" },
    host: { name: "payments-api-2" },
    event: { id: "evt-demo-1" },
    labels: { team: "payments" },
  },
  null,
  2,
);

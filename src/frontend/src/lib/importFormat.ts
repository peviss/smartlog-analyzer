import type { ImportFormat } from "../api/client";

/** Same limits as the API (api/domain/raw_logs.py), checked before uploading. */
export const MAX_IMPORT_BYTES = 5 * 1024 * 1024;
export const MAX_IMPORT_RECORDS = 10_000;

export const IMPORT_ACCEPT = ".log,.txt,.json,.ndjson,.jsonl,.csv,.tsv,text/plain,text/csv,application/json";

/** Guess the format from the file extension, or from the content when pasted. */
export function guessFormat(fileName: string | null, content: string): ImportFormat {
  const ext = fileName?.toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
  if (ext === "csv" || ext === "tsv") return "csv";
  if (ext === "json" || ext === "ndjson" || ext === "jsonl") return "json";
  if (ext) return "text";
  // Pasted: a JSON array or object is JSON; anything else (NDJSON included) parses as text.
  const start = content.trimStart()[0];
  if (start === "[") return "json";
  if (start === "{" && !content.trim().includes("\n")) return "json";
  return "text";
}

export function countLines(content: string): number {
  return content.split(/\r?\n/).filter((l) => l.trim()).length;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** One line per supported format: none of them needs to be ECS. */
export const IMPORT_SAMPLE = [
  "2026-11-16 13:00:00,114 ERROR [main] com.shop.Payments - Could not acquire database connection",
  "\tat com.shop.db.Pool.acquire(Pool.java:88)",
  "2026-11-16 13:00:02,114 - checkout - WARNING - Retrying payment pay_123 (attempt 2)",
  "<11>1 2026-11-16T13:00:03Z web-1 checkout 912 - - Upstream stripe returned 503",
  '10.0.0.7 - - [16/Nov/2026:13:00:04 +0000] "POST /api/pay HTTP/1.1" 502 17 "-" "curl/8.5"',
  "E1116 13:00:05.123456       1 reflector.go:324] watch of *v1.Pod ended with: too old resource version",
  'time=2026-11-16T13:00:06Z level=info service=orders msg="Order ord_77 created"',
  '{"time":"2026-11-16T13:00:07Z","level":50,"msg":"Unhandled exception","service":"orders"}',
  '{"version":"1.1","host":"web-2","short_message":"Disk almost full","level":4,"_service":"storage"}',
  '{"@t":"2026-11-16T13:00:08Z","@mt":"Order {OrderId} shipped","OrderId":77,"Application":"orders"}',
  "Cache warmed in 380ms",
].join("\n");

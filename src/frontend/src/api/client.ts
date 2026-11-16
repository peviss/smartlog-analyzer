/**
 * Typed client for the SmartLog API.
 *
 * Types come from `schema.d.ts`, generated from docs/openapi.json (`npm run gen:api`), so a
 * change in the API contract breaks the build here instead of failing at runtime.
 * The UI is always served from the same origin as the API (Vite proxy in development,
 * nginx in Docker), so requests use relative paths and need no CORS.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type LogRead = Schemas["LogRead"];
export type LogPage = Schemas["LogPage"];
export type Level = Schemas["LogLevel"];
export type StatsResponse = Schemas["StatsResponse"];
export type StatsBucket = Schemas["StatsBucket"];
export type AnalysisRead = Schemas["AnalysisRead"];
export type AnalyzeRequest = Schemas["AnalyzeRequest"];
export type HealthResponse = Schemas["HealthResponse"];
export type LogBatchResult = Schemas["LogBatchResult"];
export type LogImportResult = Schemas["LogImportResult"];
export type ImportFormat = LogImportResult["format"];

export type ListQuery = NonNullable<paths["/api/logs"]["get"]["parameters"]["query"]>;
export type StatsQuery = NonNullable<paths["/api/logs/stats"]["get"]["parameters"]["query"]>;
export type GroupBy = NonNullable<StatsQuery["group_by"]>[number];
/** The filter set shared by list, stats and analysis requests. */
export type FilterQuery = Omit<Schemas["AnalysisFilters"], "limit">;

const client = createClient<paths>({ baseUrl: globalThis.location?.origin ?? "" });

/** Error with the API's envelope: `{"error": {code, message, details, ...}, "request_id"}`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = undefined,
    readonly requestId: string | null = null,
    /** Extra fields of the envelope, e.g. `analysis_id` when an LLM analysis fails. */
    readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ValidationDetail {
  field: string | null;
  location?: string | null;
  message: string | null;
  type?: string | null;
}

export function toApiError(status: number, body: unknown): ApiError {
  if (body && typeof body === "object" && "error" in body) {
    const { error, request_id } = body as { error: Record<string, unknown>; request_id?: string | null };
    if (error && typeof error === "object") {
      const { code, message, details, ...extra } = error;
      return new ApiError(
        status,
        typeof code === "string" ? code : "UNKNOWN_ERROR",
        typeof message === "string" ? message : `Error ${status}`,
        details,
        request_id ?? null,
        extra,
      );
    }
  }
  // A proxy error page (502/504 from nginx) or anything else that is not the API envelope.
  const code = status === 429 ? "RATE_LIMITED" : status === 413 ? "PAYLOAD_TOO_LARGE" : "HTTP_ERROR";
  return new ApiError(status, code, `Error HTTP ${status}`);
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<{ data: T; status: number }> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "NETWORK_ERROR", "No se pudo conectar con la API.");
  }
  if (!result.response.ok || result.error !== undefined) {
    throw toApiError(result.response.status, result.error);
  }
  return { data: result.data as T, status: result.response.status };
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  // 503 still carries a HealthResponse body: the API is up but the database is not.
  const result = await client.GET("/api/health", { signal });
  if (result.response.status === 503 && result.error) return result.error as HealthResponse;
  return (await unwrap(Promise.resolve(result))).data;
}

export async function listLogs(query: ListQuery, signal?: AbortSignal): Promise<LogPage> {
  return (await unwrap(client.GET("/api/logs", { params: { query }, signal }))).data;
}

export async function getLog(id: number, signal?: AbortSignal): Promise<LogRead> {
  return (await unwrap(client.GET("/api/logs/{log_id}", { params: { path: { log_id: id } }, signal }))).data;
}

export async function getStats(query: StatsQuery, signal?: AbortSignal): Promise<StatsResponse> {
  return (await unwrap(client.GET("/api/logs/stats", { params: { query }, signal }))).data;
}

export async function analyze(body: AnalyzeRequest): Promise<AnalysisRead> {
  return (await unwrap(client.POST("/api/logs/analyze", { body }))).data;
}

export async function getAnalysis(id: string, signal?: AbortSignal): Promise<AnalysisRead> {
  return (
    await unwrap(client.GET("/api/analysis/{analysis_id}", { params: { path: { analysis_id: id } }, signal }))
  ).data;
}

/** POST /api/logs. `created` is false when the event.id was already stored (a retry). */
export async function ingestOne(doc: Record<string, unknown>): Promise<{ log: LogRead; created: boolean }> {
  const { data, status } = await unwrap(
    client.POST("/api/logs", { body: doc as Schemas["LogCreate"] }),
  );
  return { log: data, created: status === 201 };
}

export async function ingestBatch(logs: unknown[]): Promise<LogBatchResult> {
  // Items are sent as typed: the API validates each one and reports the invalid ones.
  const body = { logs: logs as Schemas["LogCreate"][] };
  return (await unwrap(client.POST("/api/logs/batch", { body }))).data;
}

const IMPORT_CONTENT_TYPE: Record<ImportFormat, string> = {
  text: "text/plain",
  json: "application/json",
  csv: "text/csv",
};

/** POST /api/logs/import: the body is the raw file content. */
export async function importLogs(
  content: string,
  options: { format: ImportFormat; serviceName?: string; logLevel?: string },
): Promise<LogImportResult> {
  const query: NonNullable<paths["/api/logs/import"]["post"]["parameters"]["query"]> = {
    format: options.format,
  };
  if (options.serviceName?.trim()) query["service.name"] = options.serviceName.trim();
  if (options.logLevel) query["log.level"] = options.logLevel;
  return (
    await unwrap(
      client.POST("/api/logs/import", {
        params: { query },
        body: content,
        bodySerializer: (body: unknown) => body as string,
        headers: { "Content-Type": `${IMPORT_CONTENT_TYPE[options.format]}; charset=utf-8` },
      }),
    )
  ).data;
}

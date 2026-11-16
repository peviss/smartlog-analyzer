import { ApiError, type ValidationDetail } from "../api/client";

const FRIENDLY: Record<string, string> = {
  NETWORK_ERROR: "No se pudo conectar con la API. ¿Está corriendo el backend?",
  RATE_LIMITED: "Demasiadas solicitudes. Esperá unos segundos y probá de nuevo.",
  PAYLOAD_TOO_LARGE: "El archivo es demasiado grande (máximo 5 MB). Dividilo en partes.",
  INVALID_IMPORT: "",
  LLM_TIMEOUT: "El modelo no respondió a tiempo.",
  LLM_INVALID_RESPONSE: "El modelo devolvió una respuesta que no cumple el formato esperado.",
  LLM_PROVIDER_ERROR: "El proveedor del modelo devolvió un error (autenticación, límite o caída).",
};

function isValidationDetails(details: unknown): details is ValidationDetail[] {
  return Array.isArray(details) && details.every((d) => d && typeof d === "object" && "message" in d);
}

export function ErrorBanner({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : null;
  const message = apiError ? FRIENDLY[apiError.code] || apiError.message : String(error);
  return (
    <div className="banner banner-error" role="alert">
      <strong>{title ?? "No se pudo completar la operación"}</strong>
      <p>{message}</p>
      {apiError && isValidationDetails(apiError.details) && (
        <ul className="error-details">
          {apiError.details.map((d, i) => (
            <li key={i}>
              {d.field && <code>{d.field}</code>} {d.message}
            </li>
          ))}
        </ul>
      )}
      {apiError && (apiError.code !== "NETWORK_ERROR" || apiError.requestId) && (
        <p className="muted small">
          {apiError.code}
          {apiError.requestId && <> · request id <code>{apiError.requestId}</code></>}
        </p>
      )}
    </div>
  );
}

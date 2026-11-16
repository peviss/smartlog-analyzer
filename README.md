# SmartLog Analyzer

Herramienta interna para centralizar los logs de varios microservicios, consultarlos y obtener con IA un resumen, categorías de problemas y sugerencias de acción.

Los logs siguen el **Elastic Common Schema (ECS 9.4)**: se ingestan y se devuelven como documentos ECS (`@timestamp`, `log.level`, `message`, `service.name`, `trace.id`...).

- **Stack:** Python 3.13, FastAPI, PostgreSQL 16, SQLAlchemy 2 (async) + Alembic, Nginx y Docker Compose.
- **IA:** [Pydantic AI](https://ai.pydantic.dev) con salida estructurada validada por Pydantic (`AnalysisResult`). Un mock realista (`FunctionModel`) funciona sin API key y sin costo; la integración real con Anthropic Claude se activa con una variable de entorno.
- **Decisiones técnicas y diagramas:** ver [ARCHITECTURE.md](ARCHITECTURE.md).

**Contenido:** [Cómo levantar](#cómo-levantar) · [Cómo testear](#cómo-testear) · [Uso de IA](#uso-de-ia) · [API](#api) · [Contenedores](#contenedores-puertos-red-y-variables) · [Estructura](#estructura) · [Pendientes](#pendientes)

## Cómo levantar

### Con Docker (recomendado)

```bash
cp .env.example .env          # cambiá POSTGRES_PASSWORD (y DATABASE_URL si corrés fuera de Docker)
docker compose up -d --build  # o: make up
```

Esto levanta la base, aplica las migraciones, carga unos 1500 logs de ejemplo (solo si la base está vacía) y expone la interfaz web y la API detrás de Nginx.

- Interfaz web: http://localhost:8080
- API: http://localhost:8080/api
- Documentación interactiva (Swagger): http://localhost:8080/docs
- OpenAPI: http://localhost:8080/openapi.json (copia versionada en [docs/openapi.json](docs/openapi.json))

Para probar el flujo completo:

```bash
curl -s localhost:8080/api/health

# ¿Dónde están los errores?
curl -s "localhost:8080/api/logs/stats?group_by=service.name&group_by=log.level&log.level=error&log.level=critical"

# Analizar con IA los warnings y errores más recientes
curl -s -X POST localhost:8080/api/logs/analyze \
  -H 'content-type: application/json' \
  -d '{"filters": {"log.level": ["warn", "error", "critical"], "limit": 500}}'
```

### Sin Docker

Necesita un PostgreSQL 16 accesible. El código está en `src/` (layout *src*), así que hay que agregarlo al `PYTHONPATH`; el `Makefile` y `pytest` ya lo hacen solos.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export PYTHONPATH=src:.
export DATABASE_URL=postgresql+psycopg://smartlog:smartlog@localhost:5432/smartlog
alembic upgrade head                  # migraciones en db/migrations/
python -m db.seed --reset             # datos de ejemplo; opciones: --count 5000 --seed 7
uvicorn api.main:app --reload
```

### Interfaz web en desarrollo

La UI está en `src/frontend/` (React 19 + Vite + TypeScript). Necesita Node 22 o superior.

```bash
make ui-install      # npm ci
make api-dev         # API en :8000 (en otra terminal)
make ui-dev          # UI en http://localhost:5173; Vite reenvía /api a :8000
```

Tiene cuatro vistas: **Logs** (filtros ECS, detalle del documento, selección para analizar), **Estadísticas** (agregaciones por servicio, nivel, entorno, host y hora o día), **Análisis** (correr un análisis por filtros o por ids y ver categorías, sugerencias y logs de ejemplo) e **Ingesta** (un log rápido o JSON, individual o en lote). Los filtros se comparten entre vistas: lo que filtrás en Logs es lo que se analiza.

El cliente HTTP está tipado con los tipos generados desde [docs/openapi.json](docs/openapi.json). Si cambia la API, `make openapi` regenera el OpenAPI y los tipos de la UI, y `npm run typecheck` marca lo que haya que ajustar. En Docker, la imagen de Nginx compila la UI ([docker/nginx.Dockerfile](docker/nginx.Dockerfile)) y la sirve en `/`, en el mismo origen que la API, así que no hace falta CORS.

## Cómo testear

```bash
make test-unit                     # unitarios, sin base de datos
make test-db && make test          # levanta Postgres en :5433 y corre todo
# o con una base propia:
TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/smartlog_test pytest -v
```

Hay 362 tests de backend:
- **Unitarios:** ECS (claves con puntos y anidadas, conflictos, normalización de niveles, documento de salida), validación de esquemas (incluye casos borde de timestamps, tamaños y labels), extracción de patrones y el analizador con modelos simulados con `FunctionModel` de Pydantic AI (respuesta válida, texto en vez de salida estructurada, esquema incorrecto, reintento con errores de validación, ids alucinados, timeout con cancelación real y errores del proveedor). También modelos: mock, factory y `AnthropicModel` contra un transporte HTTP simulado.
- **Integración** contra PostgreSQL real: los endpoints de punta a punta, la persistencia de análisis fallidos, la paginación, los filtros, la agregación, el formato de error en 404/405/422/500, el health con la base caída, que las migraciones coincidan con los modelos y que la migración 0002 convierta datos existentes al formato ECS y vuelva atrás.

Los tests de integración se saltean si no hay base disponible.

La UI tiene sus propios tests (Vitest + Testing Library): filtros, parseo de la ingesta, el formato de error de la API, la serie temporal y las vistas de logs y de análisis con la API simulada. Se corren con `make ui-test`, que también hace el typecheck.

Lint y formato: `make lint` (ruff). La CI corre lint, la suite completa contra un Postgres de servicio y un smoke test con `docker compose`.

## Uso de IA

### En el producto

El endpoint `POST /api/logs/analyze` usa un LLM para resumir un conjunto de logs, agruparlos en categorías de problemas y proponer acciones. La integración está hecha con [Pydantic AI](https://ai.pydantic.dev):

1. **Preprocesamiento sin IA.** Los logs se agrupan en plantillas (`Could not acquire DB connection after <NUM>`) con conteos, servicios e ids de ejemplo, y se calculan estadísticas. Así 500 logs suelen quedar en 10 a 50 patrones: menos tokens, y los números los calcula el código, no el modelo.
2. **Agente con salida tipada.** Un `Agent` de Pydantic AI (`src/api/llm/agent.py`) con `output_type=AnalysisResult`. El JSON Schema de ese modelo Pydantic se envía al LLM como tool de salida y la respuesta se valida (severidades y prioridades como enums, campos obligatorios, enteros no negativos).
3. **Reintento con feedback.** Si la respuesta no cumple el esquema, Pydantic AI le devuelve los errores de validación al modelo (`LLM_OUTPUT_RETRIES`, por defecto 1). Si sigue fallando: `502 LLM_INVALID_RESPONSE`.
4. **Protecciones.** Un `output_validator` descarta `example_log_ids` que no pertenecen al análisis (alucinaciones). Los logs van dentro de `<log_data>` y el prompt indica tratarlos como datos (prompt injection). El run tiene deadline (`LLM_TIMEOUT_SECONDS`, `504 LLM_TIMEOUT`).
5. **Trazabilidad.** Cada análisis se persiste, también si falla, con proveedor, modelo, duración y la salida cruda del modelo (`raw_response`). Los tokens usados quedan en los logs de la app.

**Modelos disponibles** (`LLM_PROVIDER`):

| Valor | Qué es | Requiere |
|---|---|---|
| `mock` (default) | `FunctionModel` de Pydantic AI que aplica reglas sobre los patrones reales. Pasa por el mismo agente y la misma validación. | Nada; sin costo |
| `anthropic` | `AnthropicModel` de Pydantic AI con Claude, `temperature=0` | `LLM_API_KEY` y `LLM_MODEL` |
| `openai` | `OpenAIChatModel` con GPT, o cualquier API compatible con OpenAI (Azure, vLLM, LiteLLM) usando `LLM_BASE_URL` | `LLM_API_KEY` y `LLM_MODEL` |
| `google` | `GoogleModel` con Gemini (Gemini Developer API) | `LLM_API_KEY` y `LLM_MODEL` |
| `ollama` | `OllamaModel` con un modelo local. `LLM_BASE_URL` por defecto: `http://localhost:11434/v1` | `LLM_MODEL` (con soporte de tools, p. ej. `qwen3`); sin API key |

```env
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-5-5
```

Todos usan `temperature=0`, `LLM_MAX_TOKENS` y `LLM_TIMEOUT_SECONDS`. Agregar otro proveedor que Pydantic AI soporte (Mistral, Groq, Bedrock...) es una función más en `src/api/llm/providers.py`; ver [ARCHITECTURE.md §4](ARCHITECTURE.md#4-integración-real-con-un-llm).

### En el desarrollo

Se usó Claude Code como asistente para migrar la capa de LLM a Pydantic AI (agente, mock y dobles de test como `FunctionModel`) y para reorganizar el repositorio en `src/` y `db/`. Cada cambio se verificó con la suite completa (unitarios e integración contra PostgreSQL) y con el smoke test de `docker compose`.

## API

### Endpoints

| Método | Ruta | Descripción | Éxito | Errores |
|---|---|---|---|---|
| POST | `/api/logs` | Ingesta un documento ECS. Si el `event.id` ya existe para ese servicio (reintento), devuelve el log guardado | 201 (200 en un reintento) | 422 |
| POST | `/api/logs/import` | Importa logs crudos: texto plano, JSON o CSV (hasta 10.000 registros, 5 MB). Errores por número de línea | 201 | 413, 422 |
| POST | `/api/logs/batch` | Ingesta hasta 1000 documentos ECS. Guarda los válidos y reporta los inválidos por posición; omite los `event.id` repetidos | 201 | 422 (ninguno válido) |
| GET | `/api/logs` | Lista documentos ECS con filtros y paginación | 200 | 422 |
| GET | `/api/logs/{id}` | Un log como documento ECS | 200 | 404, 422 |
| GET | `/api/logs/stats` | Agregación por `service.name`, `log.level`, `service.environment`, `host.name` y/o periodo (hora o día) | 200 | 422 |
| POST | `/api/logs/analyze` | Analiza un conjunto de logs con el LLM y persiste el resultado | 201 | 404, 422, 502, 504 |
| GET | `/api/analysis/{id}` | Resultado de un análisis | 200 | 404, 422 |
| GET | `/api/health` | Estado del servicio y de la base | 200 | 503 |

### Ingesta: formato ECS

El endpoint acepta documentos [ECS](https://www.elastic.co/docs/reference/ecs) con claves anidadas, con claves con puntos (como las emiten las librerías *ecs-logging* de Java, .NET, Go, Node y Python) o con una mezcla de ambas. Estas dos formas son equivalentes:

```bash
# Claves con puntos (salida típica de ecs-logging)
curl -X POST localhost:8080/api/logs -H 'content-type: application/json' -d '{
  "@timestamp": "2026-11-16T13:00:00Z",
  "log.level": "ERROR",
  "message": "Could not acquire database connection after 5000ms",
  "service.name": "payments-api",
  "trace.id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "ecs.version": "9.4.0"
}'

# Objetos anidados
curl -X POST localhost:8080/api/logs -H 'content-type: application/json' -d '{
  "@timestamp": "2026-11-16T13:00:00Z",
  "log": {"level": "ERROR", "logger": "payments.service"},
  "message": "Could not acquire database connection after 5000ms",
  "service": {"name": "payments-api", "version": "1.4.2", "environment": "production"},
  "host": {"name": "payments-api-2"},
  "trace": {"id": "4bf92f3577b34da6a3ce929d0e0e4736"},
  "error": {"type": "sqlalchemy.exc.TimeoutError", "stack_trace": "..."},
  "labels": {"team": "payments"}
}'
```

La respuesta (y `GET /api/logs`) devuelve siempre el documento **anidado**, como recomienda ECS, más el `id` del recurso en SmartLog:

```json
{
  "id": 1501,
  "@timestamp": "2026-11-16T13:00:00Z",
  "log": {"level": "ERROR", "logger": "payments.service"},
  "message": "Could not acquire database connection after 5000ms",
  "service": {"name": "payments-api", "version": "1.4.2", "environment": "production"},
  "event": {"severity": 3, "ingested": "2026-11-16T13:00:01.204Z"},
  "ecs": {"version": "9.4.0"}
}
```

**Campos.** Solo `message`, `log.level` y `service.name` son obligatorios.

| Campo ECS | Reglas |
|---|---|
| `@timestamp` | ISO 8601. Si falta, se usa el momento de la ingesta (como indica ECS) y el log recibe el tag `_timestamp_missing`. Sin zona horaria se interpreta como UTC. Se rechaza si está más de 5 minutos en el futuro. |
| `log.level` | **Se guarda tal como llega** (`ERROR`, `warn`, `Information`, `FATAL`...), porque ECS define este campo como el nivel original. Si la grafía no es conocida (ver abajo) **no se rechaza**: cuenta como `info` y el log recibe el tag `_level_unrecognized`. |
| `message` | No puede estar en blanco; hasta 10.000 caracteres. |
| `service.name` | Se normaliza a minúsculas; solo `a-z 0-9 . _ -`; hasta 100 caracteres. |
| `service.version`, `service.environment`, `host.name`, `log.logger` | Opcionales, texto. |
| `trace.id`, `span.id`, `transaction.id` | Opcionales; alfanuméricos con guiones, hasta 64 caracteres. |
| `error.type`, `error.message`, `error.stack_trace` | Opcionales. |
| `event.id` | Opcional, hasta 128 caracteres. Identificador del evento en el origen: hace que los reintentos sean idempotentes. Un log con un `event.id` ya guardado para el mismo `service.name` no se guarda de nuevo. |
| `event.dataset`, `event.original` | Opcionales. `event.original` es el evento crudo. Si el emisor no lo manda y `INGEST_KEEP_ORIGINAL=true`, SmartLog guarda ahí el documento tal como llegó (útil para reprocesar o para auditoría; duplica el espacio, por eso está apagado por defecto). |
| `labels` | Objeto plano; los valores se guardan como texto (`true` pasa a `"true"`), como define ECS. Hasta 50 entradas. |
| `tags` | Lista de textos, hasta 20. SmartLog agrega las marcas `_level_unrecognized` y `_timestamp_missing` cuando tuvo que asumir un valor (convención de Elastic, como `_grokparsefailure`). |
| `ecs.version` | Se guarda la del emisor; si falta, se devuelve `9.4.0`. |
| **Cualquier otro campo** | Se acepta (ECS es un esquema permisivo), se guarda y se devuelve tal cual (por ejemplo `http.response.status_code` o `log.origin.file.line`). Hasta 8 KB en total. |
| `event.severity`, `event.ingested` | **Los calcula SmartLog**: si el cliente los manda, se ignoran. |

Si el mismo campo llega dos veces con valores distintos (por ejemplo `"log.level": "info"` y `"log": {"level": "error"}`), la request se rechaza con 422.

**Niveles.** ECS no define una lista cerrada de valores para `log.level`. SmartLog conserva el original y deriva dos valores para poder filtrar y analizar de forma uniforme:

| Nivel normalizado | `event.severity` | Grafías aceptadas en `log.level` (sin distinguir mayúsculas) |
|---|---|---|
| `critical` | 0-2 | `emerg`, `emergency`, `panic`, `alert`, `crit`, `critical`, `fatal`, `severe`, `f` |
| `error` | 3 | `err`, `error`, `e` |
| `warn` | 4 | `warn`, `warning`, `w` |
| `info` | 5-6 | `notice`, `info`, `information`, `informational`, `i` |
| `debug` | 7 | `debug`, `trace`, `verbose`, `fine`, `finer`, `finest`, `config`, `d`, `t`, `v` |

`event.severity` usa los códigos de severidad de syslog (menor es más grave). ECS deja el significado de este número al implementador, siempre que sea consistente.

Una grafía que no está en la tabla se guarda igual, como `info` (severidad 6) y con el tag `_level_unrecognized`: perder el evento es peor que equivocar su nivel. El valor original queda en `log.level`.

**Respuesta del batch.** Cada log se valida por separado, como en la bulk API de Elasticsearch:

```json
{"ingested": 2, "ids": [41, 42], "duplicates": 1, "failed": 1,
 "errors": [{"index": 3, "errors": [{"field": "logs.3.message", "location": "body", "message": "Value error, message must not be blank", "type": "value_error"}]}]}
```

Si ningún log es válido, responde 422 con el formato de error habitual. Un cliente que solo mira el código de estado tiene que revisar además `failed`.

### Importar logs crudos (`POST /api/logs/import`)

Para logs que no están en ECS: archivos de una app vieja, un `journalctl`, un export de otra herramienta. El cuerpo es el contenido del archivo; el formato sale del `Content-Type` o de `?format=text|json|csv`. Desde la UI: **Ingesta → Importar logs crudos** (arrastrar un archivo o pegar texto).

```bash
curl -X POST "localhost:8080/api/logs/import?service.name=legacy-app" \
  -H "Content-Type: text/plain" --data-binary @app.log
curl -X POST localhost:8080/api/logs/import -H "Content-Type: text/csv" --data-binary @export.csv
```

Los registros **no necesitan estar en ECS**: se parsean y se mapean a ECS ([src/api/domain/raw_logs/](src/api/domain/raw_logs/)).

| Formato | Qué reconoce |
|---|---|
| **Texto plano** (`text/plain`) | Un registro por línea: NDJSON; syslog RFC 5424 y RFC 3164; access logs de Apache/nginx (common y combined, con los campos `http.*`, `url.*`, `source.ip` y el nivel según el status); klog de Kubernetes/Go; CRI de Kubernetes (`<ts> stderr F <línea>`); logfmt; y `<timestamp> <NIVEL> <mensaje>`. De Python logging, logback y log4j también se extraen `log.logger` y `process.thread.name`. Timestamps ISO, `2026/11/16 …`, Apache, Tomcat, Java y epoch. Las líneas indentadas y `Caused by:` se agregan al registro anterior como `error.stack_trace`. La línea original queda en `event.original`. |
| **JSON** (`application/json`, `application/x-ndjson`) | Una lista, `{"logs": [...]}`, un objeto o NDJSON, en cualquier esquema. |
| **CSV** (`text/csv`) | Fila de encabezado con cualquier nombre de columna; separador `,` `;` tab o `\|`. |

**Esquemas conocidos.** Se detectan por los campos que solo ellos tienen y se traducen con su propia semántica:

| Esquema | Cómo se traduce |
|---|---|
| OpenTelemetry (OTLP/JSON) | `resourceLogs` se expande en un registro por `logRecord`; `body` y `attributes` (AnyValue) se leen; `severityNumber` → nivel; atributos de recurso → `service.*`; `exception.*` → `error.*`. |
| GELF (Graylog) | `short_message` → `message`, `full_message` → `error.stack_trace`, `_campos` → ECS si tienen nombre conocido, si no a `labels`. |
| Serilog CLEF | `@mt` se renderiza con sus propiedades (`"Order {OrderId} failed"` → `"Order 7 failed"`); `@x` → `error.*`; sin `@l` es `Information`. |
| Google Cloud Logging | `jsonPayload`/`textPayload`, `severity`, `trace` (`projects/…/traces/ID` → `trace.id`), `httpRequest`, `resource.labels` → `service.name` y `cloud.*`. |
| AWS CloudWatch | El payload de una subscription se expande por `logEvents`; `logGroup` → `service.name`; el `message` de cada evento se parsea como texto. |
| Docker json-file | La línea dentro de `log` se parsea como texto; `stream` → `labels.stream`. |

**Cualquier otro esquema** pasa por una normalización genérica que reubica lo que no encaja, en vez de rechazar el registro:
- Aplana objetos anidados y mapea nombres en cualquier estilo a ECS. Por ejemplo, `traceId`, `trace_id` y `TraceId` → `trace.id`; `level`/`severity` → `log.level`; `msg`/`text` → `message`; `time`/`timestamp` → `@timestamp`; `service`/`app` → `service.name`. Si están los dos, gana el nombre ECS.
- Un valor simple donde ECS espera un objeto se mueve a su campo principal: `"error": "…"` → `error.message`, `"event": "login"` → `event.action`.
- Los niveles numéricos de syslog (0-7) y de pino/bunyan (10-60) se traducen a nombres. El `DEFAULT` de Google Cloud cuenta como sin nivel.
- Si falta `message`, se usa `body`, `description` u otro campo parecido. Si no hay ninguno, el mensaje es el registro completo, con el tag `_message_missing`.
- Un `service.name` inválido se corrige (`"Payments API"` → `payments-api`) y el original queda en `labels.original_service_name`. Los ids que no son válidos se mueven a `labels`. Una fecha ilegible queda en `labels.original_timestamp`, con el tag `_timestamp_unparsed`.
- Los valores que superan los límites se recortan (`_truncated`). Si los campos extra superan los 8 KB, se quitan los más grandes y el registro completo queda en `event.original` (`_fields_dropped`).
- **Detección del nivel en texto libre:** una palabra cuenta como nivel solo si está en MAYÚSCULAS o entre corchetes, o si es el primer token después del timestamp. Así, `"Info about the user"` o `"... connection error"` no se toman como nivel.
- **Valores por defecto:** `?service.name=` y `?log.level=` completan los registros que no los traen. Si el nivel lo puso SmartLog (por defecto `info`), el log recibe el tag `_level_missing`.
- **Validación:** igual que en el batch, cada registro se valida por separado. Se guardan los válidos y los rechazados vuelven con `line` (la línea del archivo donde empieza el registro). La respuesta agrega `format` y `records`.
- **Límites:** 5 MB, 10.000 registros y UTF-8 (se acepta BOM). Los errores son `413 PAYLOAD_TOO_LARGE` y `422 INVALID_IMPORT`. Todo se guarda en una sola transacción.

### Filtros de `GET /api/logs` y `/stats`

Los nombres de los filtros son los de ECS.

| Parámetro | Ejemplo | Notas |
|---|---|---|
| `service.name` | `?service.name=a&service.name=b` | Admite varios valores |
| `log.level` | `?log.level=warn&log.level=error` | Compara contra el nivel normalizado: `warn` encuentra `WARN`, `warning` y `W`. Admite varios valores. |
| `service.environment`, `host.name`, `event.dataset`, `trace.id` | `?trace.id=4bf9...` | Igualdad exacta |
| `from`, `to` | `?from=2026-11-16T00:00:00Z` | Rango sobre `@timestamp`; `from` debe ser anterior o igual a `to` |
| `q` | `?q=timeout` | Búsqueda de texto en `message`, sin distinguir mayúsculas (`%` y `_` se toman literalmente) |
| `limit`, `offset` | `?limit=50&offset=100` | Solo para `/api/logs`; `limit` máximo 500 |
| `group_by` | `?group_by=service.name&group_by=hour` | Solo para `/stats`: `service.name`, `log.level`, `service.environment`, `host.name`, `hour`, `day` |

### Análisis

El cuerpo lleva **una** de estas dos fuentes:

```json
{"log_ids": [12, 13, 14]}
{"filters": {"service.name": ["payments-api"], "log.level": ["error", "critical"], "from": "2026-11-16T00:00:00Z", "limit": 200}}
```

La respuesta (201) incluye `summary`, `categories` (nombre, severidad, ocurrencias, servicios e ids de ejemplo), `suggestions` (con prioridad), `stats` calculadas sin el LLM, `provider`, `model` y `duration_ms`.

Si el LLM falla, el análisis se guarda igual con `status: "failed"`, y el error incluye `analysis_id` para consultarlo después:

| Situación | HTTP | `error.code` |
|---|---|---|
| El LLM devolvió algo que no cumple el esquema (después de los reintentos) | 502 | `LLM_INVALID_RESPONSE` |
| El LLM no respondió dentro de `LLM_TIMEOUT_SECONDS` | 504 | `LLM_TIMEOUT` |
| El proveedor falló (auth, rate limit, 5xx) | 502 | `LLM_PROVIDER_ERROR` |
| Algún `log_id` no existe | 404 | `LOGS_NOT_FOUND` (con `details.missing_ids`) |
| Los filtros no coinciden con ningún log | 422 | `NO_LOGS_MATCHED` |

### Formato de error

Todos los errores tienen la misma forma:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "The request is not valid.",
    "details": [{"field": "log.level", "location": "body", "message": "Value error, unknown log level 'LOUD'. Accepted (case-insensitive): ...", "type": "value_error"}]
  },
  "request_id": "b4028265e79d4c89885a8da868e61b98"
}
```

El `request_id` también viene en el header `X-Request-ID`; si el cliente lo envía, se respeta. Aparece además en los logs JSON de la app.

## Contenedores: puertos, red y variables

### Servicios y puertos

| Servicio | Imagen | Puerto interno | Publicado en el host |
|---|---|---|---|
| `nginx` | nginx:1.27-alpine | 80 | `${HTTP_PORT:-8080}` |
| `app` | build local (`Dockerfile`) | 8000 | no (solo vía Nginx) |
| `db` | postgres:16-alpine | 5432 | no (descomentá `ports` en el compose para usar psql) |

### Redes

- **`frontend`:** `nginx` y `app`. También le da salida a internet a la app, para llamar al proveedor de LLM.
- **`backend`:** `app` y `db`. Está marcada `internal: true`, así que la base no es alcanzable desde afuera.
- El volumen `pgdata` persiste la base. `docker compose down -v` (o `make clean`) la borra.

### Variables de entorno (`.env.example`)

| Variable | Default | Descripción |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `smartlog` / *obligatoria* / `smartlog` | Credenciales con las que se crea la base en Compose |
| `DATABASE_URL` | `postgresql+psycopg://...@localhost:5432/smartlog` | Conexión de la app. En Compose se sobrescribe con el host `db`. |
| `ENVIRONMENT` | `development` | `development`, `test` o `production` |
| `LOG_LEVEL` | `INFO` | Nivel de los logs JSON de la app |
| `PORT` | `8000` | Puerto de Uvicorn dentro del contenedor |
| `HTTP_PORT` | `8080` | Puerto publicado por Nginx en el host |
| `RUN_MIGRATIONS` | `true` | Ejecuta `alembic upgrade head` al arrancar |
| `SEED_ON_START` | `true` | Carga datos de ejemplo si la tabla de logs está vacía |
| `LLM_PROVIDER` | `mock` | Modelo de Pydantic AI: `mock` o `anthropic` |
| `LLM_MODEL` | `claude-haiku-5-5` | Modelo a usar con el proveedor real |
| `LLM_API_KEY` | vacío | Obligatoria si `LLM_PROVIDER=anthropic` |
| `LLM_TIMEOUT_SECONDS` | `30` | Tiempo máximo de espera al LLM; se cancela y devuelve 504 |
| `LLM_MAX_TOKENS` | `2000` | Tope de tokens de la respuesta |
| `LLM_OUTPUT_RETRIES` | `1` | Veces que Pydantic AI le devuelve los errores de validación al modelo antes de fallar |
| `MOCK_LLM_LATENCY_MS` | `300` | Latencia simulada del mock |
| `ANALYSIS_MAX_PATTERNS` | `50` | Máximo de patrones enviados al LLM por análisis |

## Extras incluidos

- **CI** (`.github/workflows/ci.yml`): lint con ruff, tests contra un Postgres de servicio, y build de la imagen con smoke test de `docker compose`.
- **Nginx** como reverse proxy: rate limiting, límite de body, timeout extendido para `/analyze` y propagación de `X-Request-ID`.
- **Makefile** con `make help` para ver los comandos.
- **OpenAPI** generado automáticamente, con una copia en `docs/`.
- **Paginación** con `total` y `has_more`.
- **Logging estructurado** en JSON con request id.

## Estructura

```
src/api/            código fuente (paquete `api`)
  main.py           app FastAPI: lifespan, middleware y routers
  routers/          endpoints HTTP (logs, analysis, health) y dependencias
  core/             settings, errores de dominio y logging estructurado
  database/         engine y sesión (session.py) y modelos ORM (tables.py)
  schemas/          validación de entrada y contratos de salida (logs, analysis, health)
  domain/           lógica pura: convenciones ECS y extracción de patrones
  repositories/     acceso a datos de logs y agregaciones
  services/         caso de uso de análisis
  llm/              agente Pydantic AI, prompt, analyzer, proveedores y mock
src/frontend/       interfaz web (React + Vite + TypeScript)
  src/api/          cliente tipado; schema.d.ts se genera desde docs/openapi.json
  src/pages/        Logs, Estadísticas, Análisis, Ingesta
  src/components/   filtros, gráficos, detalle de log, vista de análisis
db/
  migrations/       migraciones Alembic (0001 esquema inicial, 0002 campos ECS)
  seed.py           datos de ejemplo con incidentes
tests/              unit/ e integration/
docker/             entrypoint y configuración de Nginx
docs/openapi.json   copia versionada del OpenAPI
Dockerfile, docker-compose.yml, .env.example, alembic.ini, Makefile
```

## Pendientes

- **Autenticación:** no hay. En un entorno real, una API key por servicio productor para la ingesta y SSO para consulta y análisis.
- **Otros formatos:** endpoint OTLP (`/v1/logs`) y adaptadores para CloudWatch (subscription filter) y Azure (Event Hubs), que convierten al documento ECS antes de pasar por la misma validación.
- **Volumen:** la ingesta es sincrónica, una fila por log. A gran escala: un collector (OpenTelemetry/Fluent Bit) que envíe por lotes a `/api/logs/batch`, particionado de `log_entries` por fecha y políticas de retención.
- **Análisis en segundo plano:** con cola y polling (ver [ARCHITECTURE.md §3.7](ARCHITECTURE.md#37-análisis-sincrónico-con-deadline-persistido-como-pending-antes-de-llamar-al-llm)).
- **PII:** enmascarar emails, tokens y similares antes de persistir o de enviar al LLM.
- **Calidad del análisis:** un set de casos con resultados esperados (por ejemplo con `pydantic-evals`) para comparar modelos y prompts. La integración con Anthropic está probada contra un transporte HTTP simulado; falta una corrida periódica contra la API real.
- **Costos:** persistir los tokens por análisis (hoy solo van al log de la app) y cachear análisis con el mismo conjunto de logs.
- **Búsqueda semántica:** embeddings de los patrones de error (pgvector) para "incidentes parecidos a este".

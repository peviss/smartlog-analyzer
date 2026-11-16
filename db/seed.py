"""Seed the database with realistic sample logs (ECS documents) from five microservices.

Each service uses the level spelling of its logging library, as real services do
(Java WARN, Python WARNING, .NET Information, Go/Node lowercase), so the ECS
`log.level` keeps the original value while filtering works on the normalized level.

The data tells a story so the AI analysis has something to find:
  * steady background traffic (INFO/DEBUG) across all services over the last 24 h;
  * a database incident in `payments-api` about 3 h ago (pool exhaustion + timeouts);
  * a burst of failed logins against `auth-service`;
  * an upstream provider (stripe) returning 503s to `payments-api`;
  * a memory warning trend in `notification-worker`.

Usage:
    python -m db.seed            # adds ~1500 logs
    python -m db.seed --reset    # truncates log and analysis tables first
    python -m db.seed --count 5000 --seed 7
"""

import argparse
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, insert, text

from api.core.config import get_settings
from api.database.tables import LogEntry
from api.domain.ecs import ECS_VERSION
from api.schemas.logs import LogCreate

SERVICES = ["api-gateway", "auth-service", "orders-api", "payments-api", "notification-worker"]

# Normalized level -> how each service's logging library spells it in log.level
LEVEL_STYLE = {
    "api-gateway": {"debug": "debug", "info": "info", "warn": "warn", "error": "error", "critical": "fatal"},
    "auth-service": {"debug": "debug", "info": "info", "warn": "warn", "error": "error", "critical": "fatal"},
    "orders-api": {"debug": "DEBUG", "info": "INFO", "warn": "WARN", "error": "ERROR", "critical": "FATAL"},
    "payments-api": {
        "debug": "DEBUG",
        "info": "INFO",
        "warn": "WARNING",
        "error": "ERROR",
        "critical": "CRITICAL",
    },
    "notification-worker": {
        "debug": "Debug",
        "info": "Information",
        "warn": "Warning",
        "error": "Error",
        "critical": "Fatal",
    },
}
LOGGERS = {
    "api-gateway": "gateway.router",
    "auth-service": "auth/handler",
    "orders-api": "com.shop.orders.OrderController",
    "payments-api": "payments.service",
    "notification-worker": "Notifications.Worker",
}
TEAMS = {
    "api-gateway": "platform",
    "auth-service": "identity",
    "orders-api": "checkout",
    "payments-api": "payments",
    "notification-worker": "platform",
}
ERROR_TYPES = {
    "NullPointerException": "java.lang.NullPointerException",
    "Database query timeout": "psycopg.errors.QueryCanceled",
    "Could not acquire database connection": "sqlalchemy.exc.TimeoutError",
}

BACKGROUND = {
    "api-gateway": [
        ("info", "GET /api/products 200 in {ms}ms"),
        ("info", "POST /api/orders 201 in {ms}ms"),
        ("debug", "Routing request {rid} to orders-api"),
    ],
    "auth-service": [
        ("info", "User {user} logged in successfully"),
        ("info", "Issued access token for user {user}, expires in 3600s"),
    ],
    "orders-api": [
        ("info", "Order {order} created for user {user} with {n} items"),
        ("info", "Order {order} status changed to SHIPPED"),
        ("debug", "Cache hit for product catalog page {n}"),
    ],
    "payments-api": [
        ("info", "Payment {pay} authorized for order {order}, amount {amount} USD"),
        ("debug", "Fetched exchange rates in {ms}ms"),
    ],
    "notification-worker": [
        ("info", "Sent order confirmation email to {email}"),
        ("info", "Processed {n} messages from queue notifications"),
    ],
}

INCIDENTS = [
    # (service, level, template, hours_ago_center, spread_minutes, count)
    (
        "payments-api",
        "error",
        "Could not acquire database connection from pool after 5000ms (pool size 10, waiting 37)",
        3,
        25,
        60,
    ),
    (
        "payments-api",
        "error",
        "Database query timeout after 30s: SELECT * FROM transactions WHERE order_id = {order}",
        3,
        25,
        25,
    ),
    ("payments-api", "critical", "Payment processing halted: database unavailable for 120s", 3, 5, 3),
    ("orders-api", "warn", "Slow response from payments-api: {slow}ms for order {order}", 3, 30, 40),
    ("api-gateway", "error", "POST /api/payments 504 Gateway Timeout after 30000ms", 3, 25, 30),
    (
        "auth-service",
        "warn",
        "Failed login attempt for user {user} from 203.0.113.{ip}: invalid password",
        9,
        40,
        70,
    ),
    ("auth-service", "error", "Account {user} locked after 5 failed login attempts", 9, 40, 12),
    (
        "payments-api",
        "error",
        "Upstream provider stripe returned 503 Service Unavailable for payment {pay}",
        14,
        15,
        22,
    ),
    ("notification-worker", "warn", "Memory usage at {pct}% of container limit (512MB)", 6, 240, 18),
    (
        "orders-api",
        "error",
        "NullPointerException in OrderMapper.toDto: shippingAddress is null for order {order}",
        18,
        600,
        9,
    ),
]


def _fill(template: str, rng: random.Random) -> str:
    return template.format(
        ms=rng.randint(8, 400),
        slow=rng.randint(4000, 30000),
        rid=uuid.UUID(int=rng.getrandbits(128)).hex[:12],
        user=f"user_{rng.randint(1000, 9999)}",
        order=f"ord_{rng.randint(100000, 999999)}",
        pay=f"pay_{rng.randint(100000, 999999)}",
        amount=f"{rng.uniform(5, 900):.2f}",
        email=f"customer{rng.randint(1, 5000)}@example.com",
        n=rng.randint(1, 12),
        ip=rng.randint(1, 254),
        pct=rng.randint(78, 97),
    )


def generate(count: int, seed: int, now: datetime | None = None) -> list[dict]:
    rng = random.Random(seed)
    now = now or datetime.now(UTC)
    rows: list[dict] = []

    incident_total = sum(i[5] for i in INCIDENTS)
    for _ in range(max(count - incident_total, 0)):
        service = rng.choice(SERVICES)
        level, template = rng.choice(BACKGROUND[service])
        rows.append(
            _row(service, level, _fill(template, rng), now - timedelta(seconds=rng.randint(0, 86_400)), rng)
        )

    for service, level, template, hours_ago, spread, n in INCIDENTS:
        center = now - timedelta(hours=hours_ago)
        for _ in range(n):
            ts = center + timedelta(minutes=rng.uniform(-spread / 2, spread / 2))
            rows.append(_row(service, level, _fill(template, rng), min(ts, now), rng))

    rows.sort(key=lambda r: r["timestamp"])
    return rows


def _row(service: str, level: str, message: str, ts: datetime, rng: random.Random) -> dict:
    """Build an ECS document (dotted keys, as ecs-logging libraries emit) and return the DB row."""
    trace = uuid.UUID(int=rng.getrandbits(128)).hex
    document = {
        "@timestamp": ts.isoformat(),
        "log.level": LEVEL_STYLE[service][level],
        "message": message,
        "ecs.version": ECS_VERSION,
        "log.logger": LOGGERS[service],
        "service.name": service,
        "service.version": "1.4.2",
        "service.environment": "staging",
        "host.name": f"{service}-{rng.randint(1, 3)}",
        "trace.id": trace,
        "span.id": trace[:16],
        "event.dataset": f"{service}.app",
        "labels": {"team": TEAMS[service]},
    }
    for marker, error_type in ERROR_TYPES.items():
        if marker in message:
            document["error.type"] = error_type
            document["error.message"] = message
            if error_type.startswith("java."):
                document["error.stack_trace"] = (
                    f"{error_type}: shippingAddress is null\n"
                    "\tat com.shop.orders.OrderMapper.toDto(OrderMapper.java:42)\n"
                    "\tat com.shop.orders.OrderController.get(OrderController.java:88)"
                )
    # Going through the API schema guarantees seeded data is valid ECS input.
    return LogCreate.model_validate(document).to_row()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reset", action="store_true", help="Delete existing logs and analyses first")
    args = parser.parse_args()

    engine = create_engine(get_settings().database_url)
    rows = generate(args.count, args.seed)
    with engine.begin() as conn:
        if args.reset:
            conn.execute(text("TRUNCATE analysis_logs, analyses, log_entries RESTART IDENTITY"))
        for start in range(0, len(rows), 500):
            conn.execute(insert(LogEntry), rows[start : start + 500])
    print(f"Seeded {len(rows)} logs across {len(SERVICES)} services.")


if __name__ == "__main__":
    main()

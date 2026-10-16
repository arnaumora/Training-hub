"""Database writes for the collector."""

import os

import psycopg
from psycopg.conninfo import make_conninfo

from .parse import Reading

COLLECTOR = "llu"


def connect() -> psycopg.Connection:
    return psycopg.connect(
        make_conninfo(
            host=os.environ.get("POSTGRES_HOST", "db"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            connect_timeout=5,
        )
    )


def insert_readings(conn: psycopg.Connection, readings: list[Reading], source: str) -> int:
    """Insert readings, one per minute. An existing row for a minute always wins. Returns rows added."""
    added = 0
    with conn.cursor() as cur:
        for r in readings:
            cur.execute(
                "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (minute) DO NOTHING",
                (r.minute, r.ts, r.mg_dl, r.trend, source),
            )
            added += cur.rowcount
    return added


def record_success(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO collector_status (name, last_attempt_at, last_success_at, consecutive_failures) "
        "VALUES (%s, now(), now(), 0) "
        "ON CONFLICT (name) DO UPDATE SET last_attempt_at = now(), last_success_at = now(), "
        "consecutive_failures = 0",
        (COLLECTOR,),
    )


def record_failure(conn: psycopg.Connection, error: str) -> None:
    conn.execute(
        "INSERT INTO collector_status (name, last_attempt_at, last_error_at, last_error, consecutive_failures) "
        "VALUES (%s, now(), now(), %s, 1) "
        "ON CONFLICT (name) DO UPDATE SET last_attempt_at = now(), last_error_at = now(), "
        "last_error = EXCLUDED.last_error, "
        "consecutive_failures = collector_status.consecutive_failures + 1",
        (COLLECTOR, error[:300]),
    )

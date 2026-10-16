"""Collector tests: parsing, archive, and database writes against a real Postgres 16.

Database tests are skipped unless TEST_PG_HOST is set (same convention as api/tests).
"""

import gzip
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from collector import store
from collector.archive import archive_response
from collector.parse import parse_graph, parse_measurement

API_DIR = Path(__file__).resolve().parents[2] / "api"


def m(ts: str, value, trend=None) -> dict:
    """A measurement as LibreLinkUp sends it (extra fields included, as in real responses)."""
    out = {
        "FactoryTimestamp": ts,
        "Timestamp": ts,
        "type": 1,
        "ValueInMgPerDl": value,
        "MeasurementColor": 1,
        "GlucoseUnits": 1,
        "Value": value,
        "isHigh": False,
        "isLow": False,
    }
    if trend is not None:
        out["TrendArrow"] = trend
    return out


def response(current, history) -> dict:
    return {
        "status": 0,
        "data": {
            "connection": {"id": "x", "patientId": "y", "firstName": "A", "glucoseMeasurement": current},
            "activeSensors": [],
            "graphData": history,
        },
        "ticket": {"token": "SECRET-TOKEN", "expires": 1, "duration": 1},
    }


SAMPLE = response(
    m("10/12/2026 2:45:59 PM", 185, trend=3),
    [m("10/12/2026 2:10:58 PM", 160), m("10/12/2026 2:25:58 PM", 172), m("10/12/2026 2:40:58 PM", 181)],
)


# --- parsing ---------------------------------------------------------------------------


def test_parse_current_and_history():
    current, history = parse_graph(SAMPLE)
    assert current.ts == datetime(2026, 10, 12, 14, 45, 59, tzinfo=UTC)
    assert current.minute == datetime(2026, 10, 12, 14, 45, tzinfo=UTC)
    assert (current.mg_dl, current.trend) == (185, 3)
    assert [r.mg_dl for r in history] == [160, 172, 181]
    assert all(r.trend is None for r in history)


def test_parse_handles_am_pm_and_midnight():
    r = parse_measurement(m("10/13/2026 12:05:00 AM", 99), with_trend=False)
    assert r.ts == datetime(2026, 10, 13, 0, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad",
    [None, {}, m("not a date", 100), m("10/12/2026 2:45:59 PM", 0), m("10/12/2026 2:45:59 PM", None)],
)
def test_parse_skips_unusable_measurements(bad):
    assert parse_measurement(bad, with_trend=True) is None


def test_parse_ignores_unknown_trend_code():
    assert parse_measurement(m("10/12/2026 2:45:59 PM", 120, trend=9), with_trend=True).trend is None


def test_parse_survives_missing_sections():
    assert parse_graph({}) == (None, [])
    assert parse_graph({"data": {"connection": None, "graphData": None}}) == (None, [])


# --- archive ---------------------------------------------------------------------------


def test_archive_appends_daily_file_and_strips_ticket(tmp_path):
    t = datetime(2026, 10, 12, 14, 46, tzinfo=UTC)
    p1 = archive_response(tmp_path, SAMPLE, t)
    p2 = archive_response(tmp_path, SAMPLE, t)
    assert p1 == p2 == tmp_path / "llu" / "2026" / "10" / "2026-10-12.jsonl.gz"
    with gzip.open(p1, "rt", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 2
    assert "ticket" not in lines[0]["response"]
    assert "SECRET-TOKEN" not in gzip.open(p1, "rt", encoding="utf-8").read()
    assert "ticket" in SAMPLE  # the caller's dict is not modified


# --- database (real Postgres) ----------------------------------------------------------

needs_pg = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _admin(dbname="postgres"):
    return psycopg.connect(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=dbname,
        autocommit=True,
    )


@pytest.fixture()
def db(monkeypatch):
    from alembic import command
    from alembic.config import Config

    name = f"th_test_{uuid.uuid4().hex[:8]}"
    with _admin() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("POSTGRES_HOST", os.environ["TEST_PG_HOST"])
    monkeypatch.setenv("POSTGRES_PORT", os.environ.get("TEST_PG_PORT", "5432"))
    monkeypatch.setenv("POSTGRES_USER", os.environ.get("TEST_PG_USER", "postgres"))
    monkeypatch.setenv("POSTGRES_PASSWORD", os.environ.get("TEST_PG_PASSWORD", ""))
    monkeypatch.setenv("POSTGRES_DB", name)
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    command.upgrade(cfg, "head")
    yield name
    with _admin() as c:
        c.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@needs_pg
def test_live_then_backfill_and_repeat_poll(db):
    current, history = parse_graph(SAMPLE)
    with store.connect() as conn:
        assert store.insert_readings(conn, [current], "llu_live") == 1
        assert store.insert_readings(conn, history, "llu_graph") == 3
        # The same response again (the next poll when nothing changed) adds nothing.
        assert store.insert_readings(conn, [current], "llu_live") == 0
        assert store.insert_readings(conn, history, "llu_graph") == 0
        rows = conn.execute("SELECT mg_dl, source FROM glucose_reading ORDER BY minute").fetchall()
    assert rows == [(160, "llu_graph"), (172, "llu_graph"), (181, "llu_graph"), (185, "llu_live")]


@needs_pg
def test_coarse_point_never_replaces_live_reading(db):
    live = parse_measurement(m("10/12/2026 2:40:10 PM", 181, trend=3), with_trend=True)
    coarse = parse_measurement(m("10/12/2026 2:40:58 PM", 175), with_trend=False)  # same minute
    with store.connect() as conn:
        store.insert_readings(conn, [live], "llu_live")
        assert store.insert_readings(conn, [coarse], "llu_graph") == 0
        assert conn.execute("SELECT mg_dl, source, trend FROM glucose_reading").fetchall() == [(181, "llu_live", 3)]


@needs_pg
def test_poll_once_end_to_end(db, tmp_path, monkeypatch):
    import collector.__main__ as main

    class FakeSession:
        def fetch_graph(self):
            return SAMPLE

    monkeypatch.setattr(main, "RAW_ROOT", tmp_path / "raw")
    monkeypatch.setattr(main, "HEARTBEAT", tmp_path / "heartbeat")
    main.poll_once(FakeSession())
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM glucose_reading").fetchone()[0] == 4
        assert conn.execute("SELECT consecutive_failures FROM collector_status").fetchone()[0] == 0
    assert list((tmp_path / "raw" / "llu").rglob("*.jsonl.gz"))
    assert (tmp_path / "heartbeat").exists()


@needs_pg
def test_status_counts_failures_and_resets_on_success(db):
    with store.connect() as conn:
        store.record_failure(conn, "HTTP 503")
        store.record_failure(conn, "Timeout: read timed out")
        n, err, ok = conn.execute(
            "SELECT consecutive_failures, last_error, last_success_at FROM collector_status"
        ).fetchone()
        assert (n, err, ok) == (2, "Timeout: read timed out", None)
        store.record_success(conn)
        n, ok = conn.execute("SELECT consecutive_failures, last_success_at FROM collector_status").fetchone()
        assert n == 0 and ok is not None

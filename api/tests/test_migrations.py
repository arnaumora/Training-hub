"""Migration tests against a real Postgres 16.

Skipped unless TEST_PG_HOST is set. Each run creates and drops its own throwaway database:
  TEST_PG_HOST=127.0.0.1 TEST_PG_PORT=55432 TEST_PG_USER=th pytest
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

API_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _admin_conninfo(dbname: str = "postgres") -> str:
    return psycopg.conninfo.make_conninfo(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=dbname,
    )


@pytest.fixture()
def db(monkeypatch):
    name = f"th_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("POSTGRES_HOST", os.environ["TEST_PG_HOST"])
    monkeypatch.setenv("POSTGRES_PORT", os.environ.get("TEST_PG_PORT", "5432"))
    monkeypatch.setenv("POSTGRES_USER", os.environ.get("TEST_PG_USER", "postgres"))
    monkeypatch.setenv("POSTGRES_PASSWORD", os.environ.get("TEST_PG_PASSWORD", ""))
    monkeypatch.setenv("POSTGRES_DB", name)
    yield name
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def _alembic() -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return cfg


def _insert(conn, minute, ts, mg_dl=120, trend=3, source="llu_live"):
    conn.execute(
        "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) VALUES (%s, %s, %s, %s, %s)",
        (minute, ts, mg_dl, trend, source),
    )


def test_upgrade_downgrade_upgrade(db):
    cfg = _alembic()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with psycopg.connect(_admin_conninfo(db)) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "0021"


def test_one_row_per_minute_and_first_wins(db):
    command.upgrade(_alembic(), "head")
    with psycopg.connect(_admin_conninfo(db)) as conn:
        _insert(conn, "2026-10-12 14:45:00+00", "2026-10-12 14:45:59+00", 185, 3, "llu_live")
        # A coarse point for the same minute must not replace the live one.
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (minute) DO NOTHING",
            ("2026-10-12 14:45:00+00", "2026-10-12 14:45:30+00", 180, None, "llu_graph"),
        )
        row = conn.execute("SELECT mg_dl, source FROM glucose_reading").fetchall()
        assert row == [(185, "llu_live")]


@pytest.mark.parametrize(
    "minute,ts,mg_dl,trend,source",
    [
        ("2026-10-12 14:45:00+00", "2026-10-12 14:46:10+00", 120, 3, "llu_live"),  # minute does not match ts
        ("2026-10-12 14:45:00+00", "2026-10-12 14:45:10+00", 0, 3, "llu_live"),  # impossible value
        ("2026-10-12 14:45:00+00", "2026-10-12 14:45:10+00", 120, 9, "llu_live"),  # unknown trend code
        ("2026-10-12 14:45:00+00", "2026-10-12 14:45:10+00", 120, 3, "guess"),  # unknown source
    ],
)
def test_constraints_reject_bad_rows(db, minute, ts, mg_dl, trend, source):
    command.upgrade(_alembic(), "head")
    with psycopg.connect(_admin_conninfo(db)) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(conn, minute, ts, mg_dl, trend, source)


def test_minute_check_is_utc_regardless_of_session_timezone(db):
    command.upgrade(_alembic(), "head")
    with psycopg.connect(_admin_conninfo(db)) as conn:
        conn.execute("SET TIME ZONE 'Europe/Madrid'")
        _insert(conn, "2026-10-12 14:45:00+00", "2026-10-12 16:45:59+02")
        assert conn.execute("SELECT count(*) FROM glucose_reading").fetchone()[0] == 1

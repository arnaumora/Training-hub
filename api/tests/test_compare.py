"""Cross-run comparison (app/compare.py), against a real Postgres 16.

Activities are inserted directly (no FIT file needed: the comparison reads the activity row and the numbers
app.derive stores, which it computes on the first request)."""

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, autocommit=True)


def _activity(conn, key, sport, start, minutes, metres):
    return conn.execute(
        """INSERT INTO activity (fit_key, session_index, source, sport, start_ts, end_ts, elapsed_s, moving_s, distance_m,
               avg_hr, fit_path, fit_sha256, parser_version)
           VALUES (%s, 0, 'upload', %s, %s::timestamptz, %s::timestamptz + make_interval(mins => %s), %s * 60, %s * 60, %s,
               150, 'x', 'x', 'test') RETURNING id""",
        (key, sport, start, start, minutes, minutes, minutes, metres),
    ).fetchone()[0]


@pytest.fixture()
def client(env):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    c.dbname = name
    with _db(name) as conn:
        c.ids = {
            # 5 km, 07:00-07:30 UTC (09:00 in Madrid). Live readings every minute 06:55 to 09:30:
            # 160 falling 2 per minute to 100 at 07:30, then flat.
            "a": _activity(conn, "a", "running", "2026-10-10 07:00Z", 30, 5000),
            # 8 km, no glucose at all
            "b": _activity(conn, "b", "running", "2026-10-11 17:00Z", 40, 8000),
            # 30 km ride with 15-min history only
            "c": _activity(conn, "c", "cycling", "2026-10-12 08:00Z", 60, 30000),
        }
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, greatest(100, 160 - 2 * greatest(0, extract(epoch FROM m - '2026-10-10 07:00Z')::int / 60)), 3, 'llu_live' "
            "FROM generate_series('2026-10-10 06:55Z'::timestamptz, '2026-10-10 09:30Z', interval '1 min') m"
        )
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, source) "
            "SELECT m, m, 120, 'llu_graph' FROM generate_series('2026-10-12 08:00Z'::timestamptz, '2026-10-12 11:00Z', interval '15 min') m"
        )
    return c


def _runs(client, query=""):
    r = client.get(f"/api/compare{query}")
    assert r.status_code == 200
    return r.json()


def test_everything_with_its_numbers_and_curve(client):
    out = _runs(client)
    assert out["sports"] == [{"sport": "running", "n": 2}, {"sport": "cycling", "n": 1}]
    a, b, c = out["runs"]  # oldest first
    assert [a["id"], b["id"], c["id"]] == [client.ids["a"], client.ids["b"], client.ids["c"]]
    assert (a["glucose_start"], a["delta"], a["rate_per_10min"], a["nadir"], a["low_coverage"]) == (160, -60, -20, 100, False)
    assert a["local_hour"] == 9.0 and a["local_time"] == "09:00"
    # The curve: [0, 0], then every reading after the start up to 2 h after the end (minute 150).
    assert a["curve"][0] == [0, 0] and [10.0, -20] in a["curve"] and a["curve"][-1] == [150.0, -60]
    assert len(a["curve"]) == 151
    # No glucose: listed, but nothing to draw.
    assert b["glucose_start"] is None and b["curve"] == []
    # 15-min history: flagged, a coarse curve.
    assert c["low_coverage"] is True and c["curve"] == [[0, 0]] + [[m, 0] for m in (15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0, 135.0, 150.0, 165.0, 180.0)]


def test_filters(client):
    assert [r["id"] for r in _runs(client, "?sport=running")["runs"]] == [client.ids["a"], client.ids["b"]]
    assert [r["id"] for r in _runs(client, "?sport=running&min_km=6")["runs"]] == [client.ids["b"]]
    assert [r["id"] for r in _runs(client, "?max_km=6")["runs"]] == [client.ids["a"]]
    assert _runs(client, "?sport=swimming")["runs"] == []
    # The filter does not change the sport list.
    assert len(_runs(client, "?sport=swimming")["sports"]) == 2


def test_days(client):
    # 'days' counts back from now: an activity from 100 days ago is outside 90 and inside 120.
    with _db(client.dbname) as conn:
        old = conn.execute(
            """INSERT INTO activity (fit_key, session_index, source, sport, start_ts, end_ts, elapsed_s, distance_m,
                   fit_path, fit_sha256, parser_version)
               VALUES ('old', 0, 'upload', 'running', now() - interval '100 days', now() - interval '100 days' + interval '30 min',
                   1800, 5000, 'x', 'x', 'test') RETURNING id"""
        ).fetchone()[0]
    assert old not in [r["id"] for r in _runs(client, "?days=90")["runs"]]
    assert old in [r["id"] for r in _runs(client, "?days=120")["runs"]]


def test_similar(client):
    out = client.get("/api/compare/similar?glucose=150").json()
    # a started at 160 (10 away), c at 120 (30 away); b has no glucose, so it is not a candidate at all.
    assert [r["id"] for r in out["runs"]] == [client.ids["a"], client.ids["c"]]
    assert [r["distance"] for r in out["runs"]] == [0.33, 1.0] and out["skipped"] == 0
    assert out["summary"] == {"n": 2, "median_delta": -30, "lowest": 100, "went_low": 0} and out["low"] == 70
    assert [r["id"] for r in client.get("/api/compare/similar?glucose=150&sport=cycling").json()["runs"]] == [client.ids["c"]]
    # Nothing was logged, so no run has an IOB: asked for one, every candidate is skipped (unknown is not 0).
    out = client.get("/api/compare/similar?glucose=150&iob=0").json()
    assert out["runs"] == [] and out["skipped"] == 2
    assert client.get("/api/compare/similar").status_code == 422
    assert client.get("/api/compare/similar?glucose=20").status_code == 422
    # before: only runs that started earlier (the run page asks this for one run, so it never finds itself).
    c_start = int(datetime(2026, 10, 12, 8, tzinfo=UTC).timestamp())
    assert [r["id"] for r in client.get(f"/api/compare/similar?glucose=150&before={c_start}").json()["runs"]] == [client.ids["a"]]


def test_now(client):
    with _db(client.dbname) as conn:
        conn.execute("INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
                     "VALUES (date_trunc('minute', now()) - interval '2 min', now() - interval '2 min', 142, 4, 'llu_live')")
    client.post("/api/entries", json={"ts": "2026-01-01T12:00:00Z", "carbs": {"grams": 5}})  # the log starts long ago
    ts = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
    assert client.post("/api/entries", json={"ts": ts, "carbs": {"grams": 30}}).status_code == 201
    now = client.get("/api/compare/now").json()
    assert (now["glucose"], now["trend"], now["glucose_age_min"]) == (142, 4, 2)
    assert now["min_since_last_meal"] == 45 and now["log_covered"] is True
    assert now["iob"] is None  # no Settings > Insulin saved: no IOB, never a made-up one
    assert now["min_since_last_bolus"] is None  # no rapid dose logged


def test_now_with_an_old_reading(client):
    with _db(client.dbname) as conn:
        conn.execute("INSERT INTO glucose_reading (minute, ts, mg_dl, source) "
                     "VALUES (date_trunc('minute', now()) - interval '40 min', now() - interval '40 min', 150, 'llu_graph')")
    now = client.get("/api/compare/now").json()
    assert now["glucose"] is None and now["glucose_age_min"] == 40 and now["log_covered"] is False


def test_needs_login(env):
    c, _ = env
    assert c.get("/api/compare").status_code == 401
    assert c.get("/api/compare/similar?glucose=150").status_code == 401
    assert c.get("/api/compare/now").status_code == 401

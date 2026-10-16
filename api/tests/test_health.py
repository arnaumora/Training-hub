"""/health/glucose and /api/glucose against a real Postgres 16 (skipped unless TEST_PG_HOST is set)."""

import os

import psycopg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _admin(dbname="postgres"):
    return psycopg.connect(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=dbname,
        autocommit=True,
    )


def _sql(name, sql):
    with _admin(name) as c:
        c.execute(sql)


def test_never_ran_is_503(env):
    client, _ = env
    r = client.get("/health/glucose")
    assert r.status_code == 503
    assert r.json()["status"] == "collector_never_succeeded"


def test_recent_success_is_200_even_with_old_readings(env):
    client, name = env
    _sql(name, "INSERT INTO collector_status (name, last_success_at) VALUES ('llu', now() - interval '1 minute')")
    # Newest reading 3 h old: LibreLink closed on the phone. Not an outage.
    _sql(
        name,
        "INSERT INTO glucose_reading (minute, ts, mg_dl, source) "
        "VALUES (date_trunc('minute', now() - interval '3 hours'), "
        "date_trunc('minute', now() - interval '3 hours') + interval '10 seconds', 120, 'llu_graph')",
    )
    r = client.get("/health/glucose")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["readings"]["newest_age_s"] > 3 * 3600 - 120
    assert body["readings"]["newest_live_age_s"] is None
    assert "mg_dl" not in r.text  # no glucose values on an unauthenticated endpoint


def test_stale_success_is_503(env):
    client, name = env
    _sql(
        name,
        "INSERT INTO collector_status (name, last_success_at, consecutive_failures) "
        "VALUES ('llu', now() - interval '10 minutes', 7)",
    )
    r = client.get("/health/glucose")
    assert r.status_code == 503
    assert r.json()["status"] == "collector_failing"
    assert r.json()["collector"]["consecutive_failures"] == 7


def test_does_not_require_login(env):
    client, _ = env
    assert client.get("/health/glucose").status_code != 401


# --- /api/glucose (same database fixture) -------------------------------------------------


def _login(client):
    assert client.post("/api/login", json={"password": "irrelevant-password"}).status_code == 204


def test_glucose_requires_login(env):
    client, _ = env
    assert client.get("/api/glucose").status_code == 401


def test_glucose_window_order_coverage_and_latest(env):
    client, name = env
    # Live readings 10 and 9 minutes ago, a coarse point 30 minutes ago, and one 30 hours ago (outside 24 h).
    for minutes_ago, value, source, trend in [
        (10, 140, "llu_live", "4"),
        (9, 150, "llu_live", "5"),
        (30, 120, "llu_graph", "NULL"),
        (30 * 60, 99, "llu_graph", "NULL"),
    ]:
        _sql(
            name,
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) VALUES ("
            f"date_trunc('minute', now() - interval '{minutes_ago} minutes'), "
            f"date_trunc('minute', now() - interval '{minutes_ago} minutes') + interval '5 seconds', "
            f"{value}, {trend}, '{source}')",
        )
    _login(client)
    body = client.get("/api/glucose?hours=24").json()
    assert [p[1] for p in body["points"]] == [120, 140, 150]  # oldest first, 30 h point excluded
    assert [p[2] for p in body["points"]] == [False, True, True]
    assert body["coverage"] == {"live_minutes": 2, "minutes": 1440}
    assert body["latest"]["mg_dl"] == 150 and body["latest"]["trend"] == 5 and body["latest"]["live"] is True
    assert body["to"] - body["from"] == 24 * 3600


def test_glucose_rejects_out_of_range_window(env):
    client, _ = env
    _login(client)
    assert client.get("/api/glucose?hours=0").status_code == 422
    assert client.get("/api/glucose?hours=73").status_code == 422


def test_glucose_empty_database(env):
    client, _ = env
    _login(client)
    body = client.get("/api/glucose").json()
    assert body["points"] == [] and body["latest"] is None

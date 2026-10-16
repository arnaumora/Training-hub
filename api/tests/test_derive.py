"""Per-run numbers stored and kept fresh (app/derive.py), against a real Postgres 16.

The run fixture (tests/data/run.fit) runs 07:00 to 07:30 UTC on 10 Oct 2026: 3 m/s, HR 130 -> 165."""

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from conftest import TEST_PASSWORD

from app import derive

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

from test_activities import RUN  # noqa: E402

START = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)


def _db(name):
    return psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                           user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                           dbname=name, autocommit=True)


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    c.dbname = name
    with _db(name) as conn:
        # Live readings every minute 06:55 to 09:30: 160 falling 2/min to 100 at 07:30, then flat at 100.
        conn.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "SELECT m, m, greatest(100, 160 - 2 * greatest(0, extract(epoch FROM m - '2026-10-10 07:00Z')::int / 60)), 2, 'llu_live' "
            "FROM generate_series('2026-10-10 06:55Z'::timestamptz, '2026-10-10 09:30Z', interval '1 min') m"
        )
    c.entry = lambda body: c.post("/api/entries", json=body)
    return c


def _graph(client, aid):
    return client.get(f"/api/activities/{aid}/graph").json()["metrics"]


def test_numbers_are_stored_on_import(client):
    # Something logged days earlier, so the run is inside the log's time.
    assert client.entry({"ts": "2026-10-08T12:00:00Z", "carbs": {"grams": 10}}).status_code == 201
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    with _db(client.dbname) as conn:
        g = conn.execute("SELECT glucose_start, glucose_end, delta, rate_per_10min, nadir, coverage_pct FROM run_glucose_metrics WHERE activity_id = %s", (aid,)).fetchone()
    assert g == (160, 100, -60, -20, 100, 100)
    m = _graph(client, aid)
    t = m["training"]
    assert sum(t["time_in_zone"]) == 1800 and t["time_in_zone"][0] > 0 and t["time_in_zone"][1] > 0  # 130 -> 165 crosses 162
    assert t["hr_drift_pct"] > 10  # same speed, HR up by ~18 bpm: clear decoupling
    assert abs(t["avg_pace_s"] - 333.3) < 1 and t["grade_adjusted_pace_s"] is not None
    c = m["context"]
    assert c["log_covered"] is True and c["local_time"] == "09:00" and c["pre_run_carbs"] == 0 and c["glucose_start"] == 160
    flags = client.get(f"/api/activities/{aid}/graph").json()["flags"]
    assert [f["kind"] for f in flags] == ["log"]  # 160 at the start, full coverage: only "no rapid insulin logged"


def test_a_run_before_the_log_started_has_no_log_numbers(client):
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    c = _graph(client, aid)["context"]
    assert c["log_covered"] is False and c["pre_run_carbs"] is None and c["iob_at_start"] is None


def test_log_changes_recompute_the_runs_near_them(client):
    client.entry({"ts": "2026-10-08T12:00:00Z", "carbs": {"grams": 10}})
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    # A gel 10 min before the start, logged after the run was imported (the "add pre-run carbs" case).
    r = client.entry({"ts": "2026-10-10T06:50:00Z", "carbs": {"grams": 25}})
    with _db(client.dbname) as conn:  # read the table, not the page: the page would refresh by itself
        assert conn.execute("SELECT pre_run_carbs, min_since_last_meal FROM run_context WHERE activity_id = %s", (aid,)).fetchone() == (25, 10)
    # Moving it 3 h earlier makes it neither pre-run nor the last 10 min.
    ts = "2026-10-10T03:50:00Z"
    client.put("/api/entries", json={"ts": ts, "carbs_id": r.json()["carbs"]["id"], "carbs": {"grams": 25}})
    with _db(client.dbname) as conn:
        assert conn.execute("SELECT pre_run_carbs, min_since_last_meal FROM run_context WHERE activity_id = %s", (aid,)).fetchone() == (0, 190)
    client.delete(f"/api/entries?carbs_id={r.json()['carbs']['id']}")
    with _db(client.dbname) as conn:
        assert conn.execute("SELECT min_since_last_meal FROM run_context WHERE activity_id = %s", (aid,)).fetchone() == (None,)


def test_iob_uses_the_insulin_settings_and_the_calculator(client):
    client.post("/api/settings/insulin", json={
        "meal_slots": [{"name": "All day", "start": "00:00", "ratio": 1}], "isf": 40, "correct_above": 160, "correct_to": 160,
        "action_time_h": 5, "action_time_provisional": True, "pen_increment": 1, "racion_g": 10, "rapid_insulin": "Humalog"})
    client.entry({"ts": "2026-10-10T04:30:00Z", "dose": {"units": 4, "kind": "rapid", "insulin_name": "Humalog"}})
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    c = _graph(client, aid)["context"]
    # 4 u injected 150 min before, linear over 5 h: half left
    assert c["iob_at_start"] == 2 and c["min_since_last_bolus"] == 150 and c["insulin_settings_version"] == 1


def test_new_training_settings_make_the_numbers_stale(client):
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    before = _graph(client, aid)["training"]
    assert before["training_settings_version"] == 2
    client.post("/api/settings/training", json={"lthr": 191, "max_hr": 214, "resting_hr": 49,
                                                 "hr_zone_floors": [140, 171, 181, 191], "glucose_low": 70,
                                                 "glucose_high": 180, "lag_min": 8})
    with psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                         user=os.environ.get("TEST_PG_USER", "postgres"), dbname=client.dbname, row_factory=psycopg.rows.dict_row) as conn:
        assert derive.stale(conn) == [aid]
    after = _graph(client, aid)["training"]  # opening the page recomputes it
    assert after["training_settings_version"] == 3 and after["time_in_zone"][1] > before["time_in_zone"][1]


def test_note(client):
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    assert client.put(f"/api/activities/{aid}/note", json={"note": "  gel at 30 min, felt flat  "}).json() == {"note": "gel at 30 min, felt flat"}
    assert _graph(client, aid)["note"] == "gel at 30 min, felt flat"
    client.put(f"/api/activities/{aid}/note", json={"note": ""})
    assert _graph(client, aid)["note"] is None
    assert client.put("/api/activities/999999/note", json={"note": "x"}).status_code == 404


def test_rebuild_everything(client, capsys):
    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    with _db(client.dbname) as conn:
        conn.execute("DELETE FROM run_glucose_metrics")
    assert derive.main(["--all"]) == 0 and "recomputed 1 activities" in capsys.readouterr().out
    with _db(client.dbname) as conn:
        assert conn.execute("SELECT count(*) FROM run_glucose_metrics WHERE activity_id = %s", (aid,)).fetchone() == (1,)

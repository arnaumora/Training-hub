"""/api/calculate and doses saved with their suggestion, against a real Postgres 16
(skipped unless TEST_PG_HOST is set). The arithmetic itself is covered by test_calculator.py."""

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

# Test values only. One slot for the whole day, so the result does not depend on the clock.
SETTINGS = {
    "meal_slots": [{"name": "All day", "start": "00:00", "ratio": 2}],
    "isf": 40,
    "correct_above": 160,
    "correct_to": 160,
    "action_time_h": 5,
    "action_time_provisional": False,
    "pen_increment": 1,
    "racion_g": 10,
    "rapid_insulin": "Humalog",
}


def _db(name):
    return psycopg.connect(
        host=os.environ["TEST_PG_HOST"],
        port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"),
        password=os.environ.get("TEST_PG_PASSWORD", ""),
        dbname=name,
        autocommit=True,
    )


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture()
def client(env):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    c.dbname = name
    return c


@pytest.fixture()
def ready(client):
    """Settings saved and a fresh 200 mg/dL reading."""
    assert client.post("/api/settings/insulin", json=SETTINGS).status_code == 201
    with _db(client.dbname) as c:
        c.execute(
            "INSERT INTO glucose_reading (minute, ts, mg_dl, trend, source) "
            "VALUES (date_trunc('minute', now() - interval '1 minute'), date_trunc('minute', now() - interval '1 minute'), 200, 3, 'llu_live')"
        )
    return client


def test_requires_login(env):
    c, _ = env
    assert c.post("/api/calculate", json={}).status_code == 401


def test_without_settings_it_says_so(client):
    body = client.post("/api/calculate", json={"grams": 30}).json()
    assert body["available"] is False and "Settings" in body["reason"]


def test_suggestion_from_database_inputs(ready):
    # 30 g = 3 rac * 2 = 6. Correction (200 - 160) / 40 = 1.0. 2 u 1 h ago: IOB 2 * 0.8 = 1.6, of which
    # 1.0 offsets the correction. Total 6 -> 6. A 5 u dose 6 h ago is past the action time and ignored.
    ready.post("/api/entries", json={"ts": _iso(60), "dose": {"units": 2, "kind": "rapid", "insulin_name": "Humalog"}})
    ready.post("/api/entries", json={"ts": _iso(360), "dose": {"units": 5, "kind": "rapid", "insulin_name": "Humalog"}})
    r = ready.post("/api/calculate", json={"grams": 30}).json()["result"]
    assert r["carbs_units"] == 6 and r["correction_units"] == 1 and r["iob_used"] == 1
    assert r["iob_units"] == pytest.approx(1.6, abs=0.01)
    assert r["total_units"] == 6 and r["suggested_units"] == 6
    assert r["glucose"]["mg_dl"] == 200 and r["slot"] == "All day"


def test_raciones_and_no_carbs(ready):
    # 3 raciones = 30 g: 6 + correction 1.0 = 7 -> 7. Without carbs: correction only, 1.
    assert ready.post("/api/calculate", json={"raciones": 3}).json()["result"]["suggested_units"] == 7
    assert ready.post("/api/calculate", json={}).json()["result"]["suggested_units"] == 1
    assert ready.post("/api/calculate", json={"grams": 10, "raciones": 1}).status_code == 422


def test_young_log_warning_until_14_days_of_rapid_doses(ready):
    # The test reading is 200, above correct_above: there is a correction, so a young log is flagged.
    r = ready.post("/api/calculate", json={"grams": 30}).json()["result"]
    assert "active" not in r and any("under 14 days" in w for w in r["warnings"])
    ready.post("/api/entries", json={"ts": _iso(15 * 24 * 60), "dose": {"units": 3, "kind": "rapid", "insulin_name": "Humalog"}})
    r = ready.post("/api/calculate", json={"grams": 30}).json()["result"]
    assert not any("under 14 days" in w for w in r["warnings"])


def test_basal_doses_do_not_count_as_active_insulin(ready):
    ready.post("/api/entries", json={"ts": _iso(30), "dose": {"units": 20, "kind": "basal", "insulin_name": "Lantus"}})
    r = ready.post("/api/calculate", json={}).json()["result"]
    assert r["iob_units"] == 0 and r["suggested_units"] == 1


def test_dose_saved_with_its_suggestion_keeps_it_through_edits(ready):
    # No earlier dose: 30 g -> 6 u plus correction 1.0 -> suggested 7.
    calc = ready.post("/api/calculate", json={"grams": 30}).json()["result"]
    assert calc["suggested_units"] == 7
    e = ready.post(
        "/api/entries",
        json={"carbs": {"grams": 30}, "dose": {"units": 6, "kind": "rapid", "insulin_name": "Humalog", "calc": calc}},
    )
    assert e.status_code == 201 and e.json()["dose"]["settings_version"] == calc["settings_version"]
    d = e.json()["dose"]
    # Edit the units: the stored suggestion stays as it was.
    r = ready.put(
        "/api/entries",
        json={
            "ts": _iso(0),
            "carbs_id": e.json()["carbs"]["id"],
            "dose_id": d["id"],
            "carbs": {"grams": 30},
            "dose": {"units": 5, "kind": "rapid", "insulin_name": "Humalog"},
        },
    )
    assert r.status_code == 200
    with _db(ready.dbname) as c:
        units, version, stored = c.execute("SELECT units, settings_version, calc FROM insulin_dose WHERE id = %s", (d["id"],)).fetchone()
    assert float(units) == 5 and version == calc["settings_version"] and stored["suggested_units"] == 7


@pytest.mark.parametrize(
    "dose",
    [
        {"units": 6, "kind": "basal", "insulin_name": "Lantus", "calc": {"settings_version": 1}},
        {"units": 6, "kind": "rapid", "insulin_name": "Humalog", "calc": {"suggested_units": 6}},
        {"units": 6, "kind": "rapid", "insulin_name": "Humalog", "calc": {"settings_version": 99}},
    ],
)
def test_bad_suggestions_are_refused(ready, dose):
    assert ready.post("/api/entries", json={"dose": dose}).status_code == 422
    assert ready.get("/api/log").json()["doses"] == []

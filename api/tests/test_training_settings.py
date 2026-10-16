"""Settings > Training: seeded version 1, new versions, validation, append-only, and where it is served."""

import os

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

GOOD = {"lthr": 188, "max_hr": 212, "resting_hr": 48, "hr_zone_floors": [159, 169, 178, 188],
        "glucose_low": 75, "glucose_high": 170, "lag_min": 6, "note": "after the 5K"}


@pytest.fixture()
def client(env):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    c.dbname = name
    return c


def test_seeded_versions_hold_the_run_log_values(client):
    body = client.get("/api/settings/training").json()
    cur = body["current"]
    # Version 1 (step 7) and version 2, the same plus the pace zones (step 9, change 3).
    assert cur["version"] == 2 and [h["version"] for h in body["history"]] == [2, 1]
    assert (cur["lthr"], cur["max_hr"], cur["resting_hr"], cur["hr_zone_floors"]) == (191, 214, 49, [162, 171, 181, 191])
    assert (cur["glucose_low"], cur["glucose_high"], cur["lag_min"]) == (70, 180, 8)
    assert cur["pace_zones"] == {"easy": [355, 385], "long": [360, 390], "steady": [325, 335], "threshold": [290, 300], "race": [315, 330]}
    assert body["history"][1]["pace_zones"] == {} and body["pace_zone_names"]["race"] == "Half marathon pace"


def test_saving_makes_a_new_version(client):
    r = client.post("/api/settings/training", json=GOOD)
    assert r.status_code == 201 and r.json()["version"] == 3 and r.json()["note"] == "after the 5K"
    body = client.get("/api/settings/training").json()
    assert body["current"]["hr_zone_floors"] == [159, 169, 178, 188] and [h["version"] for h in body["history"]] == [3, 2, 1]
    assert client.post("/api/settings/training", json=GOOD).status_code == 409  # nothing changed


@pytest.mark.parametrize(
    "change",
    [
        {"hr_zone_floors": [159, 159, 178, 188]},  # not increasing
        {"hr_zone_floors": [159, 169, 178, 215]},  # zone 5 above max
        {"hr_zone_floors": [159, 169, 178]},  # three floors
        {"resting_hr": 190},  # resting above LTHR
        {"max_hr": 180},  # LTHR above max
        {"hr_zone_floors": [45, 169, 178, 188]},  # zone 2 below resting
        {"lag_min": 45},
        {"glucose_low": 40},
        {"pace_zones": {"easy": [385, 355]}},  # slower first
        {"pace_zones": {"easy": [355]}},
        {"pace_zones": {"recovery": [400, 420]}},  # not a zone
        {"pace_zones": {"easy": [100, 385]}},  # 1:40 /km
    ],
)
def test_impossible_settings_are_refused(client, change):
    assert client.post("/api/settings/training", json={**GOOD, **change}).status_code == 422


def test_pace_zones_saved(client):
    zones = {"easy": [350, 380], "tempo": [310, 320]}
    assert client.post("/api/settings/training", json={**GOOD, "pace_zones": zones}).status_code == 201
    assert client.get("/api/settings/training").json()["current"]["pace_zones"] == zones
    assert client.post("/api/settings/training", json={**GOOD, "pace_zones": zones}).status_code == 409


def test_append_only(client):
    with psycopg.connect(host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
                         user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""),
                         dbname=client.dbname, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE training_settings SET lthr = 150")


def test_served_with_glucose_and_graphs(client):
    assert client.get("/api/glucose?hours=6").json()["target"] == {"low": 70, "high": 180}
    client.post("/api/settings/training", json=GOOD)
    assert client.get("/api/glucose?hours=6").json()["target"] == {"low": 75, "high": 170}
    from test_activities import RUN

    aid = client.post("/api/activities/fit", content=RUN).json()["activities"][0]["id"]
    s = client.get(f"/api/activities/{aid}/graph").json()["settings"]
    assert s["hr_zone_floors"] == [159, 169, 178, 188] and s["lag_min"] == 6


def test_needs_login(env):
    c, _ = env
    assert c.get("/api/settings/training").status_code == 401

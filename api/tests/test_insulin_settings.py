"""Settings > Insulin against a real Postgres 16 (skipped unless TEST_PG_HOST is set)."""

import os

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

# Test values only.
VALID = {
    "meal_slots": [
        {"name": "Dinner", "start": "20:00", "ratio": 1.0},
        {"name": "Breakfast", "start": "07:00", "ratio": 2.5},
        {"name": "Lunch", "start": "13:00", "ratio": 1.6},
    ],
    "isf": 40,
    "correct_above": 160,
    "correct_to": 160,
    "action_time_h": 5,
    "action_time_provisional": True,
    "pen_increment": 1,
    "racion_g": 10,
    "rapid_insulin": " Humalog ",
    "note": "first version",
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


@pytest.fixture()
def client(env):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    c.dbname = name
    return c


@pytest.mark.parametrize(
    "method,path", [("get", "/api/settings/insulin"), ("get", "/api/settings/insulin/history"), ("post", "/api/settings/insulin")]
)
def test_requires_login(env, method, path):
    c, _ = env
    assert getattr(c, method)(path).status_code == 401


def test_nothing_saved_means_no_current_version(client):
    body = client.get("/api/settings/insulin").json()
    assert body["current"] is None
    assert body["usual"]["isf"]["min"] == 15 and body["usual"]["isf"]["max"] == 120
    assert client.get("/api/settings/insulin/history").json() == []


def test_save_creates_version_with_sorted_slots(client):
    r = client.post("/api/settings/insulin", json=VALID)
    assert r.status_code == 201
    v = r.json()
    assert v["version"] == 1 and v["note"] == "first version" and v["rapid_insulin"] == "Humalog"
    assert [s["name"] for s in v["meal_slots"]] == ["Breakfast", "Lunch", "Dinner"]
    assert v["isf"] == 40 and v["correct_above"] == 160 and v["action_time_provisional"] is True
    assert v["pen_increment"] == 1 and v["racion_g"] == 10
    assert client.get("/api/settings/insulin").json()["current"]["version"] == 1


def test_every_save_is_a_new_version_and_history_keeps_all(client):
    client.post("/api/settings/insulin", json=VALID)
    client.post("/api/settings/insulin", json={**VALID, "isf": 45, "note": "endo visit"})
    current = client.get("/api/settings/insulin").json()["current"]
    assert current["version"] == 2 and current["isf"] == 45
    history = client.get("/api/settings/insulin/history").json()
    assert [h["version"] for h in history] == [2, 1] and history[1]["isf"] == 40


@pytest.mark.parametrize(
    "change",
    [
        {"meal_slots": []},
        {"meal_slots": [{"name": "A", "start": "7:00", "ratio": 1}]},
        {"meal_slots": [{"name": "A", "start": "24:00", "ratio": 1}]},
        {"meal_slots": [{"name": "A", "start": "07:00", "ratio": 0}]},
        {"meal_slots": [{"name": "A", "start": "07:00", "ratio": 1.234}]},
        {"meal_slots": [{"name": " ", "start": "07:00", "ratio": 1}]},
        {"meal_slots": [{"name": "A", "start": "07:00", "ratio": 1}, {"name": "a", "start": "12:00", "ratio": 1}]},
        {"meal_slots": [{"name": "A", "start": "07:00", "ratio": 1}, {"name": "B", "start": "07:00", "ratio": 1}]},
        {"isf": 0},
        {"correct_to": 170},  # above correct_above (160)
        {"action_time_h": 0},
        {"pen_increment": 2},
        {"racion_g": 0},
        {"rapid_insulin": "  "},
        {"isf": None},
    ],
)
def test_invalid_values_are_rejected_and_nothing_saved(client, change):
    assert client.post("/api/settings/insulin", json={**VALID, **change}).status_code == 422
    assert client.get("/api/settings/insulin").json()["current"] is None


def test_missing_field_is_rejected(client):
    body = {k: v for k, v in VALID.items() if k != "isf"}
    assert client.post("/api/settings/insulin", json=body).status_code == 422


def test_unusual_values_need_a_second_confirmation(client):
    unusual = {**VALID, "isf": 200, "meal_slots": [{"name": "Breakfast", "start": "07:00", "ratio": 6}]}
    r = client.post("/api/settings/insulin", json=unusual)
    assert r.status_code == 409
    fields = {u["field"] for u in r.json()["unusual"]}
    assert fields == {"isf", "Breakfast ratio"}
    assert client.get("/api/settings/insulin").json()["current"] is None
    r = client.post("/api/settings/insulin", json={**unusual, "confirm_unusual": True})
    assert r.status_code == 201 and r.json()["isf"] == 200


def test_versions_cannot_be_changed_or_deleted(client):
    client.post("/api/settings/insulin", json=VALID)
    with _db(client.dbname) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("UPDATE insulin_settings SET isf = 10")
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM insulin_settings")

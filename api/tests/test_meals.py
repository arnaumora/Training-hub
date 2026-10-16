"""Meals built from foods, against a real Postgres 16 (skipped unless TEST_PG_HOST is set).

Foods are inserted directly with test values; the CIQUAL importer has its own tests.
"""

import os

import psycopg
import pytest
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


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
    ids = {}
    with _db(name) as conn:
        for key, en, carbs, kcal, protein in [
            ("pasta", "Pasta, cooked", 31.4, 167, 6.1),
            ("chicken", "Chicken breast, cooked", 0, 150, 30.0),
            ("sauce", "Tomato sauce", None, 40, None),
        ]:
            ids[key] = conn.execute(
                "INSERT INTO food (source, source_id, source_version, name_en, search_text, carbs, kcal, protein) "
                "VALUES ('ciqual', %s, 'test', %s, lower(%s), %s, %s, %s) RETURNING id",
                (key, en, en, carbs, kcal, protein),
            ).fetchone()[0]
    c.food = ids
    return c


def _meal(client, *items):
    return {"items": [{"food_id": client.food[k], "grams": g} for k, g in items]}


def test_meal_entry_totals_and_items(client):
    # Pasta 150 g: 150 * 31.4 / 100 = 47.1 g carbs; chicken 120 g: 0. Total 47.1 g.
    # kcal 150 * 1.67 + 120 * 1.5 = 250.5 + 180 = 430.5; protein 9.15 + 36 = 45.15 -> 45.2 (rounded to 0.1).
    r = client.post("/api/entries", json={"carbs": {"meal": _meal(client, ("pasta", 150), ("chicken", 120)), "description": "lunch"}})
    assert r.status_code == 201
    c = r.json()["carbs"]
    assert c["entry"] == "meal" and c["grams"] == 47.1 and c["meal_id"] is not None and c["description"] == "lunch"
    m = c["meal"]
    assert [(i["name"], i["grams"], i["carbs"]) for i in m["items"]] == [("Pasta, cooked", 150, 47.1), ("Chicken breast, cooked", 120, 0)]
    assert m["totals"]["carbs"] == 47.1 and m["totals"]["kcal"] == 430.5 and m["totals"]["protein"] == 45.2
    assert "fibre" in m["incomplete"] and "carbs" not in m["incomplete"]
    log = client.get("/api/log").json()
    assert log["carbs"][0]["meal"]["totals"]["carbs"] == 47.1 and log["last_carb_entry"] == "meal"


def test_meal_with_a_dose_in_one_tap(client):
    r = client.post(
        "/api/entries",
        json={"carbs": {"meal": _meal(client, ("pasta", 200))}, "dose": {"units": 6, "kind": "rapid", "insulin_name": "Humalog"}},
    )
    assert r.status_code == 201 and r.json()["carbs"]["grams"] == 62.8 and r.json()["dose"]["units"] == 6


def test_zero_carb_meal_is_allowed(client):
    c = client.post("/api/entries", json={"carbs": {"meal": _meal(client, ("chicken", 200))}}).json()["carbs"]
    assert c["grams"] == 0 and c["meal"]["totals"]["kcal"] == 300


def test_food_without_carb_value_is_refused_and_nothing_is_stored(client):
    r = client.post("/api/entries", json={"carbs": {"meal": _meal(client, ("pasta", 100), ("sauce", 80))}})
    assert r.status_code == 422 and "Tomato sauce" in r.json()["detail"]
    with _db(client.dbname) as conn:
        assert conn.execute("SELECT (SELECT count(*) FROM meal), (SELECT count(*) FROM carb_intake)").fetchone() == (0, 0)


@pytest.mark.parametrize(
    "carbs",
    [
        {"meal": {"items": []}},
        {"meal": {"items": [{"food_id": 999999, "grams": 100}]}},
        {"meal": {"items": [{"food_id": 1, "grams": 0}]}},
        {"grams": 30, "meal": {"items": [{"food_id": 1, "grams": 100}]}},
    ],
)
def test_bad_meals_are_refused(client, carbs):
    assert client.post("/api/entries", json={"carbs": carbs}).status_code == 422


def test_logged_meal_keeps_the_values_it_was_logged_with(client):
    c = client.post("/api/entries", json={"carbs": {"meal": _meal(client, ("pasta", 100))}}).json()["carbs"]
    with _db(client.dbname) as conn:
        conn.execute("UPDATE food SET carbs = 99 WHERE id = %s", (client.food["pasta"],))
    logged = client.get("/api/log").json()["carbs"][0]
    assert logged["id"] == c["id"] and logged["grams"] == 31.4 and logged["meal"]["totals"]["carbs"] == 31.4


def test_edit_meal_to_grams_and_back(client):
    e = client.post("/api/entries", json={"ts": None, "carbs": {"meal": _meal(client, ("pasta", 100))}}).json()
    cid, t = e["carbs"]["id"], e["carbs"]["t"]
    from datetime import datetime, timezone

    ts = datetime.fromtimestamp(t, timezone.utc).isoformat()
    r = client.put("/api/entries", json={"ts": ts, "carbs_id": cid, "carbs": {"grams": 40}}).json()["carbs"]
    assert r["entry"] == "grams" and r["meal_id"] is None and "meal" not in r
    r = client.put("/api/entries", json={"ts": ts, "carbs_id": cid, "carbs": {"meal": _meal(client, ("pasta", 50))}}).json()["carbs"]
    assert r["entry"] == "meal" and r["grams"] == 15.7 and r["meal"]["items"][0]["grams"] == 50

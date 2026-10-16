"""Own food names and usage-based ranking, against a real Postgres 16 (skipped unless TEST_PG_HOST is set)."""

import os

import psycopg
import pytest
from conftest import TEST_PASSWORD

from app import ciqual_import as ci

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

PASTAS = [
    ("p1", "Pasta, dry, regular, cooked, no added salt", 31.4),
    ("p2", "Pasta, fresh, with egg, cooked, no added salt", 32.0),
    ("p3", "Pasta, dry, wholewheat, cooked, no added salt", 31.3),
    ("p4", "Pasta (e.g. ravioli), filled, cooked (average)", 25.6),
]


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
    c.food = {}
    with _db(name) as conn:
        for key, en, carbs in PASTAS:
            c.food[key] = conn.execute(
                "INSERT INTO food (source, source_id, source_version, name_en, search_text, carbs, kcal) "
                "VALUES ('ciqual', %s, 'test', %s, lower(%s), %s, 150) RETURNING id",
                (key, en, en, carbs),
            ).fetchone()[0]
    return c


def _eat(client, key, grams):
    r = client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": client.food[key], "grams": grams}]}}})
    assert r.status_code == 201
    return r.json()["carbs"]


def test_own_name_is_shown_searched_and_cleared(client):
    r = client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": "  Macarrones "})
    assert r.status_code == 200
    f = r.json()
    assert f["name"] == "Macarrones" and f["own_name"] == "Macarrones" and f["source_name"].startswith("Pasta, dry, regular")
    hits = client.get("/api/foods?q=macarrones").json()
    assert [h["id"] for h in hits] == [client.food["p1"]]
    assert client.get("/api/foods?q=MACARRONES").json()[0]["name"] == "Macarrones"  # case and accents ignored
    # The source names still find it too.
    assert client.food["p1"] in [h["id"] for h in client.get("/api/foods?q=pasta regular").json()]
    cleared = client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": ""}).json()
    assert cleared["own_name"] is None and cleared["name"].startswith("Pasta, dry, regular")
    assert client.get("/api/foods?q=macarrones").json() == []


def test_own_name_ranks_first(client):
    client.put(f"/api/foods/{client.food['p3']}/name", json={"own_name": "Pasta integral"})
    assert client.get("/api/foods?q=pasta").json()[0]["id"] == client.food["p3"]


def test_foods_eaten_more_often_rank_higher(client):
    _eat(client, "p2", 120)
    _eat(client, "p2", 150)
    _eat(client, "p4", 200)
    ids = [h["id"] for h in client.get("/api/foods?q=pasta").json()]
    assert ids[:2] == [client.food["p2"], client.food["p4"]]
    top = client.get("/api/foods?q=pasta").json()[0]
    assert top["uses"] == 2 and top["last_grams"] == 150


def test_deleted_entries_do_not_count(client):
    c = _eat(client, "p4", 200)
    client.delete(f"/api/carbs/{c['id']}")
    assert client.get("/api/foods?q=pasta").json()[0]["uses"] == 0
    assert client.get("/api/foods/frequent").json() == []


def test_frequent_foods(client):
    _eat(client, "p1", 100)
    _eat(client, "p2", 120)
    _eat(client, "p2", 130)
    freq = client.get("/api/foods/frequent").json()
    assert [(f["id"], f["uses"], f["last_grams"]) for f in freq] == [(client.food["p2"], 2, 130), (client.food["p1"], 1, 100)]


def test_meal_items_keep_the_own_name_they_were_logged_with(client):
    client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": "Macarrones"})
    c = _eat(client, "p1", 150)
    assert c["meal"]["items"][0]["name"] == "Macarrones"
    client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": "Espaguetis"})
    assert client.get("/api/log").json()["carbs"][0]["meal"]["items"][0]["name"] == "Macarrones"


def test_reimport_keeps_own_names(client, tmp_path):
    # A CIQUAL release that contains a food with source_id "p1": the import updates it but keeps the own name.
    import test_ciqual_import as t

    client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": "Macarrones"})

    foods, compo = t.FOODS, t.COMPO
    try:
        t.FOODS = [("p1", "P\u00e2tes cuites", "Pasta, cooked (new name)", "03")]
        t.COMPO = [("p1", "31000", "30,0"), ("p1", "328", "150")]
        assert ci.main([str(t.write_release(tmp_path / "r"))]) == 0
    finally:
        t.FOODS, t.COMPO = foods, compo
    f = client.get("/api/foods?q=macarrones").json()[0]
    assert f["own_name"] == "Macarrones" and f["source_name"] == "Pasta, cooked (new name)" and f["per100"]["carbs"] == 30


def test_bad_names_and_unknown_food(client):
    assert client.put(f"/api/foods/{client.food['p1']}/name", json={"own_name": "x" * 61}).status_code == 422
    assert client.put("/api/foods/999999/name", json={"own_name": "x"}).status_code == 404


def test_needs_login(env):
    c, _ = env
    assert c.get("/api/foods/frequent").status_code == 401
    assert c.put("/api/foods/1/name", json={"own_name": "x"}).status_code == 401

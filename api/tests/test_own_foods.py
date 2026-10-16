"""Own foods, recipes and the "your foods" list, against a real Postgres 16 (skipped unless TEST_PG_HOST is set).

Expected recipe values are worked out by hand in the comments, from the module docstring's formula.
"""

import os
from datetime import datetime, timezone

import psycopg
import pytest
from conftest import TEST_PASSWORD

from app import ciqual_import as ci

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")

# (key, name, carbs, protein, fat, fibre, kcal) per 100 g; "rice" has no fibre value.
CIQUAL = [
    ("pasta", "Pasta, cooked", 30.0, 5.0, 1.0, 2.0, 150.0),
    ("rice", "Rice, cooked", 28.0, 2.5, 0.5, None, 130.0),
    ("nocarb", "Mystery food", None, 1.0, 1.0, 1.0, 10.0),
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
        for key, en, carbs, protein, fat, fibre, kcal in CIQUAL:
            c.food[key] = conn.execute(
                "INSERT INTO food (source, source_id, source_version, name_en, search_text, carbs, protein, fat, fibre, kcal) "
                "VALUES ('ciqual', %s, 'test', %s, lower(%s), %s, %s, %s, %s, %s) RETURNING id",
                (key, en, en, carbs, protein, fat, fibre, kcal),
            ).fetchone()[0]
    return c


TOMATO = {"name": "Tomate frito Mercadona", "per100": {"carbs": 8.5, "sugars": 6, "fibre": 1.2, "protein": 1.5, "fat": 3.4, "salt": 1.1, "kcal": 72}}


def _own(client, body=TOMATO):
    r = client.post("/api/own-foods", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _recipe(client, items, batch_g=None, name="Macarrones con tomate"):
    return client.post("/api/recipes", json={"name": name, "items": items, "batch_g": batch_g})


# --- own foods ---------------------------------------------------------------------------------


def test_own_food_is_stored_found_first_and_listed(client):
    f = _own(client)
    assert f["source"] == "own" and f["name"] == "Tomate frito Mercadona" and f["own_name"] is None
    assert f["per100"] == {"kcal": 72.0, "carbs": 8.5, "sugars": 6.0, "fibre": 1.2, "protein": 1.5, "fat": 3.4, "salt": 1.1}
    assert client.get("/api/foods?q=tomate").json()[0]["id"] == f["id"]
    assert [x["id"] for x in client.get("/api/foods/mine").json()] == [f["id"]]


def test_own_foods_rank_with_own_names(client):
    f = _own(client, {"name": "Pasta fresca Rana", "per100": {"carbs": 45}})
    assert client.get("/api/foods?q=pasta").json()[0]["id"] == f["id"]


def test_only_carbs_are_required(client):
    f = _own(client, {"name": "Galletas", "per100": {"carbs": 70}})
    assert f["per100"]["carbs"] == 70 and f["per100"]["kcal"] is None and f["per100"]["fat"] is None
    assert client.post("/api/own-foods", json={"name": "X", "per100": {"fat": 3}}).status_code == 422


@pytest.mark.parametrize(
    "per100,why",
    [
        ({"carbs": 101}, ""),
        ({"carbs": 10, "sugars": 12}, "sugars"),
        ({"carbs": 60, "protein": 20, "fat": 20, "fibre": 5}, "add up"),
        ({"carbs": 10, "kcal": 950}, ""),
        ({"carbs": -1}, ""),
        ({"carbs": 10.123}, ""),
    ],
)
def test_impossible_values_are_refused(client, per100, why):
    r = client.post("/api/own-foods", json={"name": "Typo", "per100": per100})
    assert r.status_code == 422 and why in r.text


def test_label_rounding_is_allowed(client):
    # Sugar: 100 g carbs. Rounded labels can add up to slightly over 100 g.
    assert client.post("/api/own-foods", json={"name": "Azucar", "per100": {"carbs": 100, "sugars": 100}}).status_code == 201
    assert client.post("/api/own-foods", json={"name": "Barrita", "per100": {"carbs": 60.4, "protein": 10.3, "fat": 25.4, "fibre": 5.5}}).status_code == 201


def test_names_are_required_and_trimmed(client):
    assert client.post("/api/own-foods", json={"name": "   ", "per100": {"carbs": 5}}).status_code == 422
    assert client.post("/api/own-foods", json={"name": "x" * 61, "per100": {"carbs": 5}}).status_code == 422
    assert _own(client, {"name": "  Pan   de molde ", "per100": {"carbs": 45}})["name"] == "Pan de molde"


def test_editing_an_own_food_keeps_past_meals(client):
    f = _own(client)
    r = client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": f["id"], "grams": 200}]}}})
    assert r.json()["carbs"]["grams"] == 17.0  # 200 * 8.5 / 100
    body = {"name": "Tomate frito", "per100": {**TOMATO["per100"], "carbs": 10}}
    edited = client.put(f"/api/own-foods/{f['id']}", json=body).json()
    assert edited["name"] == "Tomate frito" and edited["per100"]["carbs"] == 10
    meal = client.get("/api/log").json()["carbs"][0]["meal"]
    assert meal["items"][0]["name"] == "Tomate frito Mercadona" and meal["items"][0]["per100"]["carbs"] == 8.5
    assert meal["items"][0]["source"] == "own"


def test_put_only_edits_own_foods(client):
    assert client.put(f"/api/own-foods/{client.food['pasta']}", json=TOMATO).status_code == 404
    assert client.put("/api/own-foods/999999", json=TOMATO).status_code == 404


def test_own_names_only_for_ciqual_foods(client):
    f = _own(client)
    assert client.put(f"/api/foods/{f['id']}/name", json={"own_name": "Tomate"}).status_code == 409
    assert client.put(f"/api/foods/{client.food['pasta']}/name", json={"own_name": "Macarrones"}).status_code == 200


# --- recipes -----------------------------------------------------------------------------------


def test_recipe_values_with_weight_when_done(client):
    tomato = _own(client)
    r = _recipe(client, [{"food_id": client.food["pasta"], "grams": 300}, {"food_id": tomato["id"], "grams": 200}], batch_g=800)
    assert r.status_code == 201, r.text
    f = r.json()
    # carbs: 300*30/100 + 200*8.5/100 = 90 + 17 = 107 g in 800 g -> 13.375 -> 13.38 per 100 g
    # kcal:  450 + 144 = 594 in 800 g -> 74.25 -> 74.3 (one decimal)
    # fat:   3 + 6.8 = 9.8 in 800 g -> 1.225 -> 1.23 (half up)
    assert f["source"] == "recipe"
    assert f["per100"]["carbs"] == 13.38 and f["per100"]["kcal"] == 74.3 and f["per100"]["fat"] == 1.23
    # CIQUAL pasta here has no sugars or salt: the recipe has none either (unknown never counts as 0)
    assert f["per100"]["sugars"] is None and f["per100"]["salt"] is None
    assert f["recipe"]["batch_g"] == 800 and f["recipe"]["items_g"] == 500
    assert [(i["name"], i["grams"]) for i in f["recipe"]["items"]] == [("Pasta, cooked", 300), ("Tomate frito Mercadona", 200)]


def test_recipe_without_weight_uses_the_ingredients(client):
    f = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}, {"food_id": client.food["rice"], "grams": 100}]).json()
    # carbs (30 + 28) in 200 g -> 29; rice has no fibre value -> fibre unknown
    assert f["per100"]["carbs"] == 29 and f["per100"]["fibre"] is None and f["recipe"]["batch_g"] is None


def test_recipe_ingredient_rules(client):
    ok = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}]).json()
    r = _recipe(client, [{"food_id": ok["id"], "grams": 100}])
    assert r.status_code == 422 and "is a recipe" in r.text
    r = _recipe(client, [{"food_id": client.food["nocarb"], "grams": 100}])
    assert r.status_code == 422 and "no carbohydrate value" in r.text
    assert _recipe(client, [{"food_id": 999999, "grams": 100}]).status_code == 422
    assert _recipe(client, []).status_code == 422
    assert _recipe(client, [{"food_id": client.food["pasta"], "grams": 0}]).status_code == 422


def test_names_are_unique(client):
    # Two foods called the same cannot be told apart in a search or a meal: case and accents aside.
    first = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}]).json()
    r = _recipe(client, [{"food_id": client.food["pasta"], "grams": 200}], name="macarrones con TOMATE")
    assert r.status_code == 409 and "already have a food called" in r.text
    r = client.post("/api/own-foods", json={**TOMATO, "name": "Macarrones con tomate"})
    assert r.status_code == 409
    # Saving a food under its own name again is fine; an archived food frees its name.
    assert client.put(f"/api/recipes/{first['id']}", json={"name": "Macarrones con tomate", "items": [{"food_id": client.food["pasta"], "grams": 150}]}).status_code == 200
    client.delete(f"/api/foods/{first['id']}")
    assert _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}]).status_code == 201
    # Your name for a CIQUAL food counts too.
    assert client.put(f"/api/foods/{client.food['pasta']}/name", json={"own_name": "Macarrones con tomate"}).status_code == 409


def test_weight_when_done_too_low_is_refused(client):
    # pasta 30 + 5 + 1 + 2 = 38 g of macros per 100 g; 100 g of pasta "weighing" 30 g when done -> 127 g per 100 g
    r = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}], batch_g=30)
    assert r.status_code == 422 and "weight when done" in r.text


def test_recipe_is_used_in_a_meal_by_its_values(client):
    f = _recipe(client, [{"food_id": client.food["pasta"], "grams": 300}], batch_g=600).json()  # 15 g carbs per 100 g
    r = client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": f["id"], "grams": 250}]}}})
    assert r.status_code == 201 and r.json()["carbs"]["grams"] == 37.5


def test_editing_an_ingredient_recomputes_its_recipes(client):
    tomato = _own(client)
    rec = _recipe(client, [{"food_id": tomato["id"], "grams": 100}], batch_g=50).json()  # 8.5 g in 50 g -> 17
    assert rec["per100"]["carbs"] == 17
    client.put(f"/api/own-foods/{tomato['id']}", json={"name": "Tomate", "per100": {"carbs": 10}})
    after = client.get(f"/api/foods/{rec['id']}").json()
    assert after["per100"]["carbs"] == 20 and after["recipe"]["items"][0]["name"] == "Tomate"
    assert after["per100"]["protein"] is None  # the edited tomato has no protein value now


def test_editing_a_recipe_replaces_its_ingredients(client):
    rec = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}]).json()
    r = client.put(
        f"/api/recipes/{rec['id']}", json={"name": "Arroz", "items": [{"food_id": client.food["rice"], "grams": 150}], "batch_g": None}
    )
    assert r.status_code == 200
    f = r.json()
    assert f["name"] == "Arroz" and f["per100"]["carbs"] == 28 and [i["food_id"] for i in f["recipe"]["items"]] == [client.food["rice"]]
    assert client.put(f"/api/recipes/{client.food['pasta']}", json={"name": "X", "items": [{"food_id": client.food["rice"], "grams": 1}]}).status_code == 404


def test_search_can_leave_recipes_out(client):
    rec = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}], name="Pasta al horno").json()
    assert rec["id"] in [f["id"] for f in client.get("/api/foods?q=pasta").json()]
    assert rec["id"] not in [f["id"] for f in client.get("/api/foods?q=pasta&exclude_recipes=true").json()]


def test_ciqual_reimport_recomputes_recipes(client, tmp_path):
    import test_ciqual_import as t

    assert ci.main([str(t.write_release(tmp_path / "a"))]) == 0
    with _db(client.dbname) as conn:
        pasta = conn.execute("SELECT id FROM food WHERE source = 'ciqual' AND source_id = '9810'").fetchone()[0]
    rec = _recipe(client, [{"food_id": pasta, "grams": 100}], batch_g=200).json()
    assert rec["per100"]["carbs"] == 14.45  # 28.9 / 2
    compo = t.COMPO
    try:
        t.COMPO = [row if row[:2] != ("9810", "31000") else ("9810", "31000", "30,0") for row in compo]
        assert ci.main([str(t.write_release(tmp_path / "b"))]) == 0
    finally:
        t.COMPO = compo
    assert client.get(f"/api/foods/{rec['id']}").json()["per100"]["carbs"] == 15


# --- archive and the list ----------------------------------------------------------------------


def test_deleted_foods_are_hidden_but_past_meals_and_recipes_keep_working(client):
    tomato = _own(client)
    rec = _recipe(client, [{"food_id": tomato["id"], "grams": 100}]).json()
    entry = client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": tomato["id"], "grams": 100}]}}}).json()
    assert client.delete(f"/api/foods/{tomato['id']}").status_code == 204
    assert client.get("/api/foods?q=mercadona").json() == []
    assert tomato["id"] not in [f["id"] for f in client.get("/api/foods/mine").json()]
    assert client.get("/api/foods/frequent").json() == []
    assert client.get(f"/api/foods/{tomato['id']}").json()["archived"] is True
    assert client.get(f"/api/foods/{rec['id']}").json()["per100"]["carbs"] == 8.5
    # The past meal can still be edited (its items are stored again).
    ts = datetime.fromtimestamp(entry["carbs"]["t"], timezone.utc).isoformat()
    r = client.put("/api/entries", json={"ts": ts, "carbs_id": entry["carbs"]["id"], "carbs": {"meal": {"items": [{"food_id": tomato["id"], "grams": 150}]}}})
    assert r.status_code == 200
    assert client.put(f"/api/own-foods/{tomato['id']}", json=TOMATO).status_code == 409


def test_ciqual_foods_cannot_be_deleted(client):
    assert client.delete(f"/api/foods/{client.food['pasta']}").status_code == 409
    assert client.delete("/api/foods/999999").status_code == 404


def test_my_foods_list(client):
    client.put(f"/api/foods/{client.food['rice']}/name", json={"own_name": "arroz"})
    client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": client.food["pasta"], "grams": 100}]}}})
    tomato = _own(client)
    rec = _recipe(client, [{"food_id": client.food["pasta"], "grams": 100}], name="Bocadillo").json()
    mine = client.get("/api/foods/mine").json()
    # named, eaten, own and recipe; the untouched CIQUAL food is not listed. Sorted by name, case ignored.
    assert [f["name"] for f in mine] == ["arroz", "Bocadillo", "Pasta, cooked", "Tomate frito Mercadona"]
    assert {f["source"] for f in mine} == {"ciqual", "recipe", "own"}
    assert client.food["nocarb"] not in [f["id"] for f in mine] and rec["id"] in [f["id"] for f in mine] and tomato


def test_frequent_and_search_routes_still_win(client):
    assert client.get("/api/foods/frequent").status_code == 200
    assert client.get("/api/foods/mine").status_code == 200
    assert client.get("/api/foods/notanumber").status_code == 422


def test_needs_login(env):
    c, _ = env
    assert c.get("/api/foods/mine").status_code == 401
    assert c.post("/api/own-foods", json=TOMATO).status_code == 401
    assert c.post("/api/recipes", json={}).status_code == 401
    assert c.delete("/api/foods/1").status_code == 401

"""Barcode lookup and Open Food Facts parsing. Parsing tests always run; the endpoint tests need TEST_PG_HOST.

Open Food Facts is never called: `barcode.fetch` is replaced by a fake that answers like the v2 product API.
"""

import gzip
import json
import os
from decimal import Decimal

import pytest
from conftest import TEST_PASSWORD
from fastapi import HTTPException

from app import barcode as bc

TOMATO = "8410188012009"
STABILO = "4006381333931"
UPC = "036000291452"  # UPC-A, stored as 0036000291452

OFF_TOMATO = {
    "code": TOMATO,
    "status": 1,
    "status_verbose": "product found",
    "product": {
        "product_name_es": "Tomate frito",
        "product_name": "Tomate frito",
        "brands": "Hacendado,Mercadona",
        "nutriments": {
            "energy-kcal_100g": 72,
            "carbohydrates_100g": 8.5,
            "sugars_100g": "6",
            "fiber_100g": 1.2,
            "proteins_100g": 1.5,
            "fat_100g": 3.4,
            "salt_100g": 1.1,
        },
    },
}
NOT_FOUND = {"code": STABILO, "status": 0, "status_verbose": "product not found"}


# --- codes and parsing (no database) -------------------------------------------------------------


@pytest.mark.parametrize("code", [TOMATO, STABILO, "96385074", UPC, "0" + STABILO])
def test_valid_check_digits(code):
    assert bc.gtin_check_ok(code)


@pytest.mark.parametrize(
    "raw,expected",
    [(TOMATO, TOMATO), (" 8410188 012009 ", TOMATO), (UPC, "0" + UPC), ("0" + STABILO, STABILO), ("96385074", "96385074")],
)
def test_normalize(raw, expected):
    assert bc.normalize_barcode(raw) == expected


@pytest.mark.parametrize("raw,why", [("8410188012008", "check digit"), ("12345", "digits"), ("84101880120a9", "digits")])
def test_bad_codes(raw, why):
    with pytest.raises(HTTPException) as e:
        bc.normalize_barcode(raw)
    assert e.value.status_code == 422 and why in e.value.detail


def test_parse_product():
    name, values, problems = bc.parse_product(OFF_TOMATO["product"])
    assert name == "Tomate frito, Hacendado" and problems == []
    assert values["carbs"] == Decimal("8.50") and values["sugars"] == Decimal("6.00") and values["kcal"] == Decimal("72.0")


def test_parse_product_energy_in_kj_bad_values_and_brand_in_name():
    p = {
        "product_name": "Galletas Maria Hacendado",
        "brands": "Hacendado",
        "nutriments": {"energy_100g": 1883, "carbohydrates_100g": 71, "fat_100g": 140, "proteins_100g": -1},
    }
    name, values, problems = bc.parse_product(p)
    assert name == "Galletas Maria Hacendado"  # the brand is already in the name
    assert values["kcal"] == Decimal("450.0")  # 1883 kJ / 4.184
    assert values["fat"] is None and values["protein"] is None and len(problems) == 2


def test_parse_product_without_names_or_nutrition():
    name, values, problems = bc.parse_product({"brands": "Hacendado", "no_nutrition_data": "on"})
    assert name == "Hacendado" and all(v is None for v in values.values()) and problems


# --- endpoint ------------------------------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


@pytest.fixture()
def client(env, tmp_path, monkeypatch):
    c, name = env
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    bc._calls.clear()
    c.answers = {TOMATO: (200, OFF_TOMATO), STABILO: (404, NOT_FOUND)}
    c.fetched = []

    def fake_fetch(code):
        c.fetched.append(code)
        answer = c.answers[code]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(bc, "fetch", fake_fetch)
    c.raw = tmp_path / "raw"
    return c


def _archived(root):
    lines = []
    for f in sorted(root.glob("off/*/*/*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            lines += [json.loads(x) for x in fh]
    return lines


@db
def test_found_on_off_is_stored_archived_and_then_served_locally(client):
    r = client.get(f"/api/barcode/{TOMATO}").json()
    assert r["status"] == "found" and r["fetched"] is True
    f = r["food"]
    assert f["source"] == "off" and f["barcode"] == TOMATO and f["name"] == "Tomate frito, Hacendado"
    assert f["per100"]["carbs"] == 8.5 and f["per100"]["salt"] == 1.1
    archived = _archived(client.raw)
    assert len(archived) == 1 and archived[0]["barcode"] == TOMATO and archived[0]["response"]["product"]["brands"] == "Hacendado,Mercadona"
    again = client.get(f"/api/barcode/{TOMATO}").json()
    assert again["fetched"] is False and again["food"]["id"] == f["id"] and client.fetched == [TOMATO]
    # It is findable by name and by its digits, listed among your foods, and usable in a meal.
    assert client.get("/api/foods?q=tomate hacendado").json()[0]["id"] == f["id"]
    assert client.get("/api/foods?q=8410188").json()[0]["id"] == f["id"]
    assert f["id"] in [x["id"] for x in client.get("/api/foods/mine").json()]
    meal = client.post("/api/entries", json={"carbs": {"meal": {"items": [{"food_id": f["id"], "grams": 200}]}}})
    assert meal.status_code == 201 and meal.json()["carbs"]["grams"] == 17


@db
def test_not_found(client):
    assert client.get(f"/api/barcode/{STABILO}").json() == {"status": "not_found", "barcode": STABILO}
    assert _archived(client.raw)[0]["http_status"] == 404


@db
def test_incomplete_products_come_back_with_what_is_usable(client):
    no_carbs = json.loads(json.dumps(OFF_TOMATO))
    del no_carbs["product"]["nutriments"]["carbohydrates_100g"]
    client.answers[TOMATO] = (200, no_carbs)
    r = client.get(f"/api/barcode/{TOMATO}").json()
    assert r["status"] == "incomplete" and "no carb value" in r["reason"]
    assert r["prefill"]["name"] == "Tomate frito, Hacendado" and r["prefill"]["per100"]["fat"] == 3.4 and r["prefill"]["per100"]["carbs"] is None
    bad = json.loads(json.dumps(OFF_TOMATO))
    bad["product"]["nutriments"]["sugars_100g"] = 20
    client.answers[TOMATO] = (200, bad)
    r = client.get(f"/api/barcode/{TOMATO}").json()
    assert r["status"] == "incomplete" and "sugars" in r["reason"]
    assert client.get("/api/foods?q=tomate").json() == []  # nothing stored either time


@db
def test_off_down_and_archive_not_writable(client):
    client.answers[TOMATO] = bc.OffUnavailable("timed out")
    assert client.get(f"/api/barcode/{TOMATO}").status_code == 502
    client.answers[TOMATO] = (200, OFF_TOMATO)
    client.raw.parent.mkdir(parents=True, exist_ok=True)
    client.raw.write_text("a file where the archive folder should be")
    r = client.get(f"/api/barcode/{TOMATO}")
    assert r.status_code == 503 and client.get("/api/foods?q=tomate").json() == []


@db
def test_rate_limit(client):
    for _ in range(bc.OFF_MAX_PER_MINUTE):
        assert client.get(f"/api/barcode/{STABILO}").status_code == 200
    assert client.get(f"/api/barcode/{STABILO}").status_code == 429


@db
def test_bad_code_is_refused_before_anything(client):
    assert client.get("/api/barcode/8410188012008").status_code == 422
    assert client.fetched == []


@db
def test_own_food_for_a_barcode_is_found_first_and_takes_it_over(client):
    off = client.get(f"/api/barcode/{TOMATO}").json()["food"]
    own = client.post("/api/own-foods", json={"name": "Tomate frito", "per100": {"carbs": 9}, "barcode": TOMATO})
    assert own.status_code == 201 and own.json()["barcode"] == TOMATO
    r = client.get(f"/api/barcode/{TOMATO}").json()
    assert r["food"]["id"] == own.json()["id"] and r["food"]["source"] == "own" and client.fetched == [TOMATO]
    assert client.get(f"/api/foods/{off['id']}").json()["barcode"] is None
    # A second own food cannot take it; a deleted own food gives it back to a new lookup.
    assert client.post("/api/own-foods", json={"name": "X", "per100": {"carbs": 1}, "barcode": TOMATO}).status_code == 409
    client.delete(f"/api/foods/{own.json()['id']}")
    back = client.get(f"/api/barcode/{TOMATO}").json()
    assert back["food"]["id"] == off["id"] and back["food"]["barcode"] == TOMATO and back["fetched"] is True


@db
def test_own_food_for_an_unknown_barcode(client):
    client.answers["0" + UPC] = (404, NOT_FOUND)
    assert client.get(f"/api/barcode/{UPC}").json()["status"] == "not_found"
    own = client.post("/api/own-foods", json={"name": "Snack", "per100": {"carbs": 60}, "barcode": UPC}).json()
    assert own["barcode"] == "0" + UPC
    assert client.get(f"/api/barcode/{UPC}").json()["food"]["id"] == own["id"]
    assert client.post("/api/own-foods", json={"name": "Y", "per100": {"carbs": 1}, "barcode": "123"}).status_code == 422


@db
def test_off_products_can_get_your_name(client):
    off = client.get(f"/api/barcode/{TOMATO}").json()["food"]
    r = client.put(f"/api/foods/{off['id']}/name", json={"own_name": "Tomate"})
    assert r.status_code == 200 and r.json()["name"] == "Tomate"
    assert client.delete(f"/api/foods/{off['id']}").status_code == 409


@db
def test_needs_login(env):
    c, _ = env
    assert c.get(f"/api/barcode/{TOMATO}").status_code == 401

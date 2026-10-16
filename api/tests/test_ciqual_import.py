"""CIQUAL importer: parsing tests always run; database tests need TEST_PG_HOST.

The fixture files imitate the CIQUAL XML release (field names from the official XML documentation),
including near-miss constituents that must NOT be picked (kJ energy, crude protein, starch).
"""

import os
from decimal import Decimal

from xml.sax.saxutils import escape

import psycopg
import pytest

from app import ciqual_import as ci
from conftest import TEST_PASSWORD

CONSTS = [
    ("327", "Energie, R\u00e8glement UE N\u00b0 1169/2011 (kJ/100 g)", "Energy, Regulation EU No 1169/2011 (kJ/100g)"),
    ("328", "Energie, R\u00e8glement UE N\u00b0 1169/2011 (kcal/100 g)", "Energy, Regulation EU No 1169/2011 (kcal/100g)"),
    ("25000", "Prot\u00e9ines, N x facteur de Jones (g/100 g)", "Protein, N x Jones' factor (g/100g)"),
    ("25003", "Prot\u00e9ines, N x 6.25 (g/100 g)", "Protein, crude, N x 6.25 (g/100g)"),
    ("31000", "Glucides (g/100 g)", "Carbohydrate (g/100g)"),
    ("32000", "Sucres (g/100 g)", "Sugars (g/100g)"),
    ("33110", "Amidon (g/100 g)", "Starch (g/100g)"),
    ("34100", "Fibres alimentaires (g/100 g)", "Fibres, dietary (g/100g)"),
    ("40000", "Lipides (g/100 g)", "Fat (g/100g)"),
    ("10004", "Sel chlorure de sodium (g/100 g)", "Salt (g/100g)"),
]
FOODS = [
    ("9810", "P\u00e2tes alimentaires cuites", "Pasta, cooked", "03"),
    ("7001", "Pain baguette", "Bread, baguette", "07"),
    ("13039", "Pomme, crue", "Apple, raw", "02"),
]
GROUPS = [("02", "fruits, l\u00e9gumes", "fruits, vegetables"), ("03", "produits c\u00e9r\u00e9aliers", "cereal products"), ("07", "pains", "breads")]
COMPO = [
    ("9810", "31000", "28,9"), ("9810", "328", "151"), ("9810", "34100", "1,8"), ("9810", "32000", "traces"),
    ("9810", "40000", "< 0,5"), ("9810", "25000", "5,32"), ("9810", "10004", "-"), ("9810", "33110", "27,1"),
    ("9810", "25003", "5,5"), ("9810", "327", "640"),
    ("7001", "31000", "56,4"), ("7001", "328", "272"),
    ("13039", "31000", "11,6"), ("13039", "328", "53,7"), ("13039", "34100", "1,4"),
]


def _xml(root: str, rec: str, rows: list[dict], encoding: str) -> bytes:
    body = "".join(f"<{rec}>" + "".join(f"<{k}> {escape(v)} </{k}>" for k, v in r.items()) + f"</{rec}>\n" for r in rows)
    return f'<?xml version="1.0" encoding="{encoding}"?>\n<{root}>\n{body}</{root}>\n'.encode(encoding)


def write_release(folder, encoding="windows-1252", date="2025_11_03", consts=CONSTS):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"const_{date}.xml").write_bytes(
        _xml("TABLE", "CONST", [{"const_code": c, "const_nom_fr": fr, "const_nom_eng": en} for c, fr, en in consts], encoding)
    )
    (folder / f"alim_grp_{date}.xml").write_bytes(
        _xml("TABLE", "ALIM_GRP", [{"alim_grp_code": c, "alim_grp_nom_fr": fr, "alim_grp_nom_eng": en} for c, fr, en in GROUPS], encoding)
    )
    (folder / f"alim_{date}.xml").write_bytes(
        _xml("TABLE", "ALIM", [{"alim_code": c, "alim_nom_fr": fr, "alim_nom_eng": en, "alim_grp_code": g} for c, fr, en, g in FOODS], encoding)
    )
    (folder / f"compo_{date}.xml").write_bytes(
        _xml("TABLE", "COMPO", [{"alim_code": a, "const_code": c, "teneur": t, "min": "", "max": "", "code_confiance": "A"} for a, c, t in COMPO], encoding)
    )
    return folder


# --- parsing (no database) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("28,9", Decimal("28.9")), ("151", Decimal("151")), ("-", None), ("", None), (None, None),
     ("traces", Decimal(0)), ("Traces", Decimal(0)), ("< 0,5", Decimal("0.5")), ("<10", Decimal(10)), ("n/a", None)],
)
def test_parse_value(raw, expected):
    assert ci.parse_value(raw) == expected


@pytest.mark.parametrize("encoding", ["windows-1252", "utf-8"])
def test_load_release_in_small_chunks(tmp_path, monkeypatch, encoding):
    monkeypatch.setattr(ci, "CHUNK", 64)  # force records and multi-byte characters across chunk boundaries
    version, consts, rows = ci.load(write_release(tmp_path / encoding, encoding))
    assert version == "ciqual-2025-11-03"
    assert {k: v["const_code"] for k, v in consts.items()} == {
        "kcal": "328", "carbs": "31000", "sugars": "32000", "fibre": "34100", "protein": "25000", "fat": "40000", "salt": "10004"
    }
    pasta = next(r for r in rows if r["source_id"] == "9810")
    assert pasta["name_fr"] == "P\u00e2tes alimentaires cuites" and pasta["name_en"] == "Pasta, cooked"
    assert pasta["group_en"] == "cereal products" and "pates alimentaires cuites" in pasta["search_text"]
    assert (pasta["carbs"], pasta["kcal"], pasta["fibre"], pasta["sugars"], pasta["fat"], pasta["protein"], pasta["salt"]) == (
        Decimal("28.9"), Decimal("151"), Decimal("1.8"), Decimal(0), Decimal("0.5"), Decimal("5.32"), None
    )
    bread = next(r for r in rows if r["source_id"] == "7001")
    assert bread["fibre"] is None and bread["carbs"] == Decimal("56.4")
    assert len(rows) == 3


def test_missing_carbohydrate_stops_the_import(tmp_path):
    folder = write_release(tmp_path / "r", consts=[c for c in CONSTS if c[0] != "31000"])
    with pytest.raises(ci.ImportProblem, match="carbs"):
        ci.load(folder)


def test_ambiguous_constituent_stops_the_import(tmp_path):
    folder = write_release(tmp_path / "r", consts=CONSTS + [("31001", "Glucides (g/100 g)", "Carbohydrate (g/100g)")])
    with pytest.raises(ci.ImportProblem, match="more than one"):
        ci.load(folder)


def test_files_from_two_releases_are_refused(tmp_path):
    folder = write_release(tmp_path / "r")
    (folder / "alim_2025_11_03.xml").rename(folder / "alim_2020_07_07.xml")
    with pytest.raises(ci.ImportProblem, match="different releases"):
        ci.load(folder)


def test_dry_run_prints_mapping_and_writes_nothing(tmp_path, capsys):
    assert ci.main([str(write_release(tmp_path / "r")), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "3 foods" in out and "carbs    <- 31000 Carbohydrate" in out and "nothing written" in out


# --- database (import and search) ---------------------------------------------------------------

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


@pytest.fixture()
def imported(env, tmp_path):
    client, name = env
    assert ci.main([str(write_release(tmp_path / "r"))]) == 0  # uses the env fixture's POSTGRES_* settings
    assert client.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    return client, name, tmp_path


@db
def test_import_is_idempotent(imported):
    client, name, tmp_path = imported
    assert ci.main([str(tmp_path / "r")]) == 0
    with psycopg.connect(
        host=os.environ["TEST_PG_HOST"], port=os.environ.get("TEST_PG_PORT", "5432"),
        user=os.environ.get("TEST_PG_USER", "postgres"), password=os.environ.get("TEST_PG_PASSWORD", ""), dbname=name,
    ) as c:
        assert c.execute("SELECT count(*), min(source_version) FROM food").fetchone() == (3, "ciqual-2025-11-03")


@db
def test_search_accents_order_and_values(imported):
    client, _, _ = imported
    assert client.get("/api/foods?q=pasta").json()[0]["name"] == "Pasta, cooked"
    hit = client.get("/api/foods?q=pates").json()[0]  # accent-free query finds "Pates alimentaires"
    assert hit["name"] == "Pasta, cooked" and hit["name_other"] == "P\u00e2tes alimentaires cuites"
    assert hit["per100"]["carbs"] == 28.9 and hit["per100"]["salt"] is None and hit["group"] == "cereal products"
    assert [f["name"] for f in client.get("/api/foods?q=raw apple").json()] == ["Apple, raw"]  # words in any order
    assert client.get("/api/foods?q=zzz").json() == []
    assert client.get("/api/foods?q=100%25").json() == []  # a literal % is not a wildcard


@db
def test_search_needs_login_and_a_query(env):
    client, _ = env
    assert client.get("/api/foods?q=pasta").status_code == 401
    assert client.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    assert client.get("/api/foods").status_code == 422
    assert client.get("/api/foods?q=a&limit=51").status_code == 422

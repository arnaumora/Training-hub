"""Food search and personal food names (roadmap step 5).

Search is local only, so it is fast, works without internet, and never hits an outside API while typing.
Every word of the query must appear (accent-free, any order) in the food's own name or its source names.
Archived own foods and recipes are never found.

Ranking, so the few foods actually eaten come before the dozens of variants in the table:
  1. own name (or the name of an own food or recipe) starting with the query, then containing it
  2. how often the food was eaten (meal items in entries that are not deleted), most first
  3. source name starting with the query, then a word starting with the first query word
  4. shorter names first
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from .common import conninfo, require_auth
from .text import normalize

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

NUTRIENTS = ("kcal", "carbs", "sugars", "fibre", "protein", "fat", "salt")

# How often each food was eaten and the weight used the last time (from meals that still count).
USAGE = """
    SELECT DISTINCT ON (mi.food_id) mi.food_id,
           count(*) OVER (PARTITION BY mi.food_id) AS uses,
           mi.grams AS last_grams,
           c.ts AS last_ts
    FROM meal_item mi
    JOIN carb_intake c ON c.meal_id = mi.meal_id AND c.deleted_at IS NULL
    ORDER BY mi.food_id, c.ts DESC, mi.id DESC
"""

# "Your" name of a food: its own name, or the name of an own food or recipe (those are yours already).
MINE = "coalesce(f.own_search, CASE WHEN f.source <> 'ciqual' THEN f.search_text END)"

SELECT = f"""
    SELECT f.id, f.source, f.source_version, f.own_name, f.name_en, f.name_fr, f.group_en, f.group_fr,
           f.batch_g, f.archived_at, f.barcode,
           {", ".join("f." + n for n in NUTRIENTS)},
           coalesce(u.uses, 0) AS uses, u.last_grams
    FROM food f
    LEFT JOIN ({USAGE}) u ON u.food_id = f.id
"""


def _esc(word: str) -> str:
    """`word` with LIKE's wildcards escaped."""
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def food_row(r: dict) -> dict:
    source_name = r["name_en"] or r["name_fr"]
    return {
        "id": r["id"],
        "source": r["source"],
        "barcode": r["barcode"],
        "name": r["own_name"] or source_name,
        "own_name": r["own_name"],
        "source_name": source_name,
        "name_other": r["name_fr"] if r["name_en"] else None,
        "group": r["group_en"] or r["group_fr"],
        "uses": r["uses"],
        "last_grams": None if r["last_grams"] is None else float(r["last_grams"]),
        "per100": {k: (None if r[k] is None else float(r[k])) for k in NUTRIENTS},
    }


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


@router.get("/foods")
def search_foods(
    q: Annotated[str, Query(min_length=1, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    exclude_recipes: bool = False,  # when picking a recipe's ingredients: recipes are never ingredients
):
    words = normalize(q).split()
    if not words:
        return []
    # Every word must be in the own name or in the source names (all words in the same one of the two).
    own_all = " AND ".join("f.own_search LIKE %s" for _ in words)
    src_all = " AND ".join("f.search_text LIKE %s" for _ in words)
    mine_all = " AND ".join(f"{MINE} LIKE %s" for _ in words)
    only = " AND f.source <> 'recipe'" if exclude_recipes else ""
    likes = ["%" + _esc(w) + "%" for w in words]
    phrase = _esc(" ".join(words)) + "%"
    query = f"""
        {SELECT}
        WHERE (({own_all}) OR ({src_all})) AND f.archived_at IS NULL{only}
        ORDER BY ({MINE} LIKE %s) DESC NULLS LAST,
                 ({mine_all}) DESC NULLS LAST,
                 coalesce(u.uses, 0) DESC,
                 (f.search_text LIKE %s) DESC,
                 (position(%s in ' ' || f.search_text) > 0) DESC,
                 length(coalesce(f.own_name, f.name_en, f.name_fr)),
                 coalesce(f.own_name, f.name_en, f.name_fr)
        LIMIT %s
    """
    params = [*likes, *likes, phrase, *likes, phrase, " " + words[0], limit]
    with _connect() as conn:
        return [food_row(r) for r in conn.execute(query, params).fetchall()]


@router.get("/foods/frequent")
def frequent_foods(limit: Annotated[int, Query(ge=1, le=30)] = 8):
    """The foods eaten most often (most recent first among equals), for picking without typing."""
    with _connect() as conn:
        rows = conn.execute(
            f"{SELECT} WHERE u.uses > 0 AND f.archived_at IS NULL ORDER BY u.uses DESC, u.last_ts DESC LIMIT %s", (limit,)
        ).fetchall()
    return [food_row(r) for r in rows]


class OwnName(BaseModel):
    own_name: Annotated[str | None, Field(max_length=60)] = None  # empty or null clears it


@router.put("/foods/{food_id}/name")
def set_own_name(food_id: int, body: OwnName):
    name = (body.own_name or "").strip() or None
    with _connect() as conn:
        found = conn.execute("SELECT source FROM food WHERE id = %s", (food_id,)).fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="food not found")
        if found["source"] not in ("ciqual", "off"):
            # Own foods and recipes already carry your name: change it in the food itself.
            raise HTTPException(status_code=409, detail="change the name of an own food or recipe in the food itself")
        if name:
            from .own_foods import check_name_free  # own_foods imports this module

            check_name_free(conn, name, food_id)
        conn.execute(
            "UPDATE food SET own_name = %s, own_search = %s WHERE id = %s", (name, normalize(name) if name else None, food_id)
        )
        return food_row(conn.execute(f"{SELECT} WHERE f.id = %s", (food_id,)).fetchone())

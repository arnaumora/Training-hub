"""Own foods, recipes and the "your foods" list (roadmap step 5).

Own food: a name and values per 100 g typed from a label. Carbs are required (they are what the calculator
uses); every other value may be left empty when the label does not give it.

Recipe: ingredients with their weights, and optionally the weight when done (batch_g, the whole pot weighed
after cooking; empty means the sum of the ingredients). Its values per 100 g are computed here, never sent
by the client:

    value per 100 g = sum(ingredient grams * ingredient value per 100 g / 100) / batch weight * 100

A value is empty when any ingredient has no value for it (an unknown never counts as 0); an ingredient
without a carb value is refused. Recipes are never ingredients, so this is one level deep.

When an ingredient's values change (an own food is edited, or a CIQUAL release is imported), every recipe
that uses it is recomputed in the same transaction. Past meals never change: they keep the values copied
when they were logged.

Typo guards: every value 0..100 g per 100 g (energy 0..900 kcal), sugars not above carbs, and carbs +
protein + fat + fibre at most 102 g per 100 g (labels round each value). For a recipe the same check
catches a weight when done that is too low for its ingredients.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from .common import conninfo, require_auth
from .foods import NUTRIENTS, SELECT, food_row
from .text import normalize

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

MAX_MACROS = Decimal(102)  # carbs + protein + fat + fibre per 100 g, with room for label rounding
MAX_KCAL = Decimal(900)
MAX_INGREDIENTS = 40

Grams100 = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


def _name(v: str) -> str:
    v = " ".join(v.split())
    if not v:
        raise ValueError("a name is required")
    return v


class Per100(BaseModel):
    carbs: Grams100
    sugars: Grams100 | None = None
    fibre: Grams100 | None = None
    protein: Grams100 | None = None
    fat: Grams100 | None = None
    salt: Grams100 | None = None
    kcal: Annotated[Decimal, Field(ge=0, le=MAX_KCAL, max_digits=6, decimal_places=1)] | None = None


class OwnFoodIn(BaseModel):
    name: Annotated[str, Field(max_length=60)]
    per100: Per100
    # Only when creating it after a scan; ignored on edit. Checked and normalized like a scanned code.
    barcode: Annotated[str, Field(max_length=20)] | None = None

    _clean = field_validator("name")(_name)


class IngredientIn(BaseModel):
    food_id: int
    grams: Annotated[Decimal, Field(gt=0, le=20000, max_digits=7, decimal_places=1)]


class RecipeIn(BaseModel):
    name: Annotated[str, Field(max_length=60)]
    items: Annotated[list[IngredientIn], Field(min_length=1, max_length=MAX_INGREDIENTS)]
    batch_g: Annotated[Decimal, Field(gt=0, le=50000, max_digits=7, decimal_places=1)] | None = None

    _clean = field_validator("name")(_name)


def check_values(values: dict, what: str) -> None:
    """Refuse values per 100 g that cannot be right (see the module docstring)."""
    carbs, sugars = values.get("carbs"), values.get("sugars")
    if sugars is not None and carbs is not None and sugars > carbs:
        raise HTTPException(status_code=422, detail=f"{what}: sugars are part of carbs, so they cannot be more than the carbs")
    macros = sum((values.get(k) or Decimal(0)) for k in ("carbs", "protein", "fat", "fibre"))
    if macros > MAX_MACROS:
        raise HTTPException(
            status_code=422, detail=f"{what}: carbs, protein, fat and fibre add up to {macros:.1f} g in 100 g, more than 100 g"
        )
    for k, v in values.items():
        top = MAX_KCAL if k == "kcal" else Decimal(100)
        if v is not None and v > top:
            raise HTTPException(status_code=422, detail=f"{what}: {k} is {v:.1f} per 100 g, more than possible")


def recipe_values(conn, items: list[tuple[int, Decimal]], batch_g: Decimal | None) -> dict:
    """Values per 100 g of a recipe from (ingredient id, grams) pairs. Raises 422 for a bad ingredient."""
    ids = [i for i, _ in items]
    foods = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, source, name_en, name_fr, own_name, {', '.join(NUTRIENTS)} FROM food WHERE id = ANY(%s)", (ids,)
        ).fetchall()
    }
    for i in ids:
        f = foods.get(i)
        if f is None:
            raise HTTPException(status_code=422, detail=f"unknown food id: {i}")
        name = f["own_name"] or f["name_en"] or f["name_fr"]
        if f["source"] == "recipe":
            raise HTTPException(status_code=422, detail=f"'{name}' is a recipe: add its ingredients instead")
        if f["carbs"] is None:
            raise HTTPException(status_code=422, detail=f"no carbohydrate value for '{name}': it cannot be an ingredient")
    total_g = sum((g for _, g in items), Decimal(0))
    weight = batch_g if batch_g is not None else total_g
    out = {}
    for n in NUTRIENTS:
        if any(foods[i][n] is None for i in ids):
            out[n] = None
            continue
        amount = sum((g * foods[i][n] / 100 for i, g in items), Decimal(0))
        # Half up, like a hand calculation (Decimal's default is half to even).
        out[n] = (amount / weight * 100).quantize(Decimal("0.1") if n == "kcal" else Decimal("0.01"), rounding=ROUND_HALF_UP)
    return out


def check_name_free(conn, name: str, food_id: int | None = None) -> None:
    """The names you give foods (own foods, recipes, your name for a CIQUAL or Open Food Facts food) are unique,
    case and accents aside: two foods called the same cannot be told apart in a search or a meal. Archived foods
    do not count. 409 if the name is taken by another food."""
    wanted = normalize(name)
    rows = conn.execute(
        """SELECT id, CASE WHEN source IN ('own', 'recipe') THEN coalesce(own_name, name_en) ELSE own_name END AS name
             FROM food
            WHERE archived_at IS NULL AND (source IN ('own', 'recipe') OR own_name IS NOT NULL)"""
    ).fetchall()
    if any(r["id"] != food_id and r["name"] and normalize(r["name"]) == wanted for r in rows):
        raise HTTPException(status_code=409, detail=f"you already have a food called '{name}'")


def _write_values(conn, food_id: int, values: dict) -> None:
    conn.execute(
        f"UPDATE food SET {', '.join(f'{n} = %({n})s' for n in NUTRIENTS)} WHERE id = %(id)s", {**values, "id": food_id}
    )


def recompute_recipes(conn, ingredient_ids: list[int] | None = None) -> int:
    """Recompute every recipe that uses one of `ingredient_ids` (all recipes when None). Returns how many.

    A recipe whose stored values no longer pass the typo guards (an ingredient changed a lot) keeps the
    computed values only if they fit the database limits; otherwise its carbs become empty, so it cannot be
    used in a meal until it is fixed, and never silently counts as something else."""
    if ingredient_ids is None:
        rows = conn.execute("SELECT id, batch_g FROM food WHERE source = 'recipe'").fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT f.id, f.batch_g FROM food f JOIN recipe_item ri ON ri.recipe_id = f.id "
            "WHERE f.source = 'recipe' AND ri.food_id = ANY(%s)",
            (ingredient_ids,),
        ).fetchall()
    for r in rows:
        items = [
            (x["food_id"], x["grams"])
            for x in conn.execute("SELECT food_id, grams FROM recipe_item WHERE recipe_id = %s ORDER BY position", (r["id"],))
        ]
        try:
            values = recipe_values(conn, items, r["batch_g"])
            check_values(values, "recipe")
        except HTTPException:
            values = {n: None for n in NUTRIENTS}
        _write_values(conn, r["id"], values)
    return len(rows)


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def _detail(conn, food_id: int) -> dict:
    r = conn.execute(f"{SELECT} WHERE f.id = %s", (food_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail="food not found")
    out = food_row(r)
    out["archived"] = r["archived_at"] is not None
    out["source_version"] = r["source_version"]
    if r["source"] == "recipe":
        items = conn.execute(
            f"""SELECT ri.food_id, ri.grams, f.source, coalesce(f.own_name, f.name_en, f.name_fr) AS name,
                       {", ".join("f." + n for n in NUTRIENTS)}
                FROM recipe_item ri JOIN food f ON f.id = ri.food_id
                WHERE ri.recipe_id = %s ORDER BY ri.position""",
            (food_id,),
        ).fetchall()
        out["recipe"] = {
            "batch_g": None if r["batch_g"] is None else float(r["batch_g"]),
            "items_g": float(sum((i["grams"] for i in items), Decimal(0))),
            "items": [
                {
                    "food_id": i["food_id"],
                    "name": i["name"],
                    "source": i["source"],
                    "grams": float(i["grams"]),
                    "per100": {n: (None if i[n] is None else float(i[n])) for n in NUTRIENTS},
                }
                for i in items
            ],
        }
    return out


def _editable(conn, food_id: int, source: str) -> None:
    r = conn.execute("SELECT source, archived_at FROM food WHERE id = %s FOR UPDATE", (food_id,)).fetchone()
    if r is None or r["source"] != source:
        raise HTTPException(status_code=404, detail=f"no {'own food' if source == 'own' else source} with id {food_id}")
    if r["archived_at"] is not None:
        raise HTTPException(status_code=409, detail="this food was deleted")


@router.get("/foods/mine")
def my_foods():
    """Your foods: own foods, recipes, foods you named and foods you have eaten. Not archived. By name."""
    with _connect() as conn:
        rows = conn.execute(
            f"""{SELECT}
                WHERE f.archived_at IS NULL
                  AND (f.source <> 'ciqual' OR f.own_name IS NOT NULL OR coalesce(u.uses, 0) > 0)
                ORDER BY lower(coalesce(f.own_name, f.name_en, f.name_fr))"""
        ).fetchall()
    return [food_row(r) for r in rows]


@router.get("/foods/{food_id}")
def food_detail(food_id: int):
    with _connect() as conn:
        return _detail(conn, food_id)


@router.post("/own-foods", status_code=201)
def create_own_food(body: OwnFoodIn):
    from .barcode import normalize_barcode  # barcode.py imports this module

    values = body.per100.model_dump()
    check_values(values, body.name)
    code = normalize_barcode(body.barcode) if body.barcode else None
    with _connect() as conn:
        check_name_free(conn, body.name)
        if code:
            holder = conn.execute(
                "SELECT id, source FROM food WHERE barcode = %s AND archived_at IS NULL FOR UPDATE", (code,)
            ).fetchone()
            if holder and holder["source"] != "off":
                raise HTTPException(status_code=409, detail="another of your foods already has this barcode")
            if holder:  # your values win over the Open Food Facts product; it stays for past meals
                conn.execute("UPDATE food SET barcode = NULL WHERE id = %s", (holder["id"],))
        food_id = conn.execute(
            f"""INSERT INTO food (source, barcode, name_en, search_text, {", ".join(NUTRIENTS)})
                VALUES ('own', %(code)s, %(name)s, %(search)s, {", ".join(f"%({n})s" for n in NUTRIENTS)}) RETURNING id""",
            {**values, "code": code, "name": body.name, "search": normalize(f"{body.name} {code or ''}")},
        ).fetchone()["id"]
        return _detail(conn, food_id)


@router.put("/own-foods/{food_id}")
def update_own_food(food_id: int, body: OwnFoodIn):
    values = body.per100.model_dump()
    check_values(values, body.name)
    with _connect() as conn:
        _editable(conn, food_id, "own")
        check_name_free(conn, body.name, food_id)
        conn.execute(
            # The barcode digits stay searchable (search_text holds them for scanned foods).
            "UPDATE food SET name_en = %s, search_text = rtrim(%s || ' ' || coalesce(barcode, '')) WHERE id = %s",
            (body.name, normalize(body.name), food_id),
        )
        _write_values(conn, food_id, values)
        recompute_recipes(conn, [food_id])
        return _detail(conn, food_id)


def _store_recipe(conn, body: RecipeIn, food_id: int | None) -> int:
    items = [(i.food_id, i.grams) for i in body.items]
    values = recipe_values(conn, items, body.batch_g)
    check_values(values, f"{body.name} (check the weight when done)")
    check_name_free(conn, body.name, food_id)
    if food_id is None:
        food_id = conn.execute(
            "INSERT INTO food (source, name_en, search_text, batch_g) VALUES ('recipe', %s, %s, %s) RETURNING id",
            (body.name, normalize(body.name), body.batch_g),
        ).fetchone()["id"]
    else:
        conn.execute(
            "UPDATE food SET name_en = %s, search_text = %s, batch_g = %s WHERE id = %s",
            (body.name, normalize(body.name), body.batch_g, food_id),
        )
        conn.execute("DELETE FROM recipe_item WHERE recipe_id = %s", (food_id,))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO recipe_item (recipe_id, position, food_id, grams) VALUES (%s, %s, %s, %s)",
            [(food_id, pos, fid, g) for pos, (fid, g) in enumerate(items)],
        )
    _write_values(conn, food_id, values)
    return food_id


@router.post("/recipes", status_code=201)
def create_recipe(body: RecipeIn):
    with _connect() as conn:
        return _detail(conn, _store_recipe(conn, body, None))


@router.put("/recipes/{food_id}")
def update_recipe(food_id: int, body: RecipeIn):
    with _connect() as conn:
        _editable(conn, food_id, "recipe")
        return _detail(conn, _store_recipe(conn, body, food_id))


@router.delete("/foods/{food_id}", status_code=204)
def archive_food(food_id: int):
    """Hide an own food or recipe from search and lists. Past meals and recipes that use it keep working."""
    with _connect() as conn:
        r = conn.execute("SELECT source FROM food WHERE id = %s", (food_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="food not found")
        if r["source"] not in ("own", "recipe"):
            # CIQUAL and Open Food Facts foods are reference data; give them your name instead.
            raise HTTPException(status_code=409, detail="only own foods and recipes can be deleted")
        conn.execute("UPDATE food SET archived_at = coalesce(archived_at, now()) WHERE id = %s", (food_id,))

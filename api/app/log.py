"""Insulin and carbs log (roadmap step 3).

Records what was injected and eaten, as typed on the phone. It never computes or suggests a dose:
that is the calculator, roadmap step 8.

- Times arrive as ISO 8601 with an offset, are stored in UTC and returned as epoch seconds.
- Deletes are soft. Every edit and delete is copied into log_audit by a database trigger
  (migration 0003), so nothing here has to remember to write the audit trail.
- The insulin name has no default in code: the UI pre-fills it from last_insulin_name,
  which is the name of the most recent logged dose of that kind.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, model_validator

from . import derive, insulin_settings
from .common import conninfo, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

# Typo guards for the entry time, not rules about when injecting or eating is allowed.
MAX_FUTURE = timedelta(minutes=5)
MAX_PAST = timedelta(days=365)

def _check_ts(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    now = datetime.now(timezone.utc)
    if value > now + MAX_FUTURE:
        raise ValueError("time is in the future")
    if value < now - MAX_PAST:
        raise ValueError("time is more than a year ago")
    return value


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


Units = Annotated[Decimal, Field(gt=0, le=100, decimal_places=1)]
Grams = Annotated[Decimal, Field(gt=0, le=1000, decimal_places=1)]
Raciones = Annotated[Decimal, Field(gt=0, le=100, decimal_places=1)]
Kind = Literal["rapid", "basal"]
Ts = Annotated[AwareDatetime | None, AfterValidator(_check_ts)]
InsulinName = Annotated[str | None, Field(max_length=40), AfterValidator(_clean)]
Note = Annotated[str | None, Field(max_length=500), AfterValidator(_clean)]
Description = Annotated[str | None, Field(max_length=200), AfterValidator(_clean)]


class DosePart(BaseModel):
    units: Units
    kind: Kind
    insulin_name: InsulinName
    note: Note = None
    # The calculator suggestion shown when this dose was logged (POST /api/calculate "result"), kept
    # with the dose so suggested and injected can be compared later. Only for new rapid doses.
    calc: dict | None = None

    @model_validator(mode="after")
    def _name_required(self):
        if self.insulin_name is None:
            raise ValueError("insulin name is required")
        if self.calc is not None:
            if self.kind != "rapid":
                raise ValueError("a calculator suggestion only goes with a rapid dose")
            if not isinstance(self.calc.get("settings_version"), int):
                raise ValueError("calc needs the settings_version it was computed with")
            if len(json.dumps(self.calc)) > 8000:
                raise ValueError("calc is too large")
        return self


class DoseIn(DosePart):
    ts: Ts = None  # None means now


class DosePatch(BaseModel):
    ts: Ts = None
    units: Units | None = None
    kind: Kind | None = None
    insulin_name: InsulinName = None
    note: Note = None

    @model_validator(mode="after")
    def _required_stay_set(self):
        # Only the note may be cleared; everything else, if sent, needs a value.
        for field in ("ts", "units", "kind", "insulin_name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty")
        return self


class MealItemIn(BaseModel):
    food_id: int
    grams: Annotated[Decimal, Field(gt=0, le=5000, decimal_places=1)]  # weight of the food eaten


class MealIn(BaseModel):
    items: Annotated[list[MealItemIn], Field(min_length=1, max_length=30)]


class CarbsPart(BaseModel):
    grams: Grams | None = None
    raciones: Raciones | None = None
    meal: MealIn | None = None  # foods with their weights; the server works out the carbs
    description: Description = None

    @model_validator(mode="after")
    def _one_amount(self):
        if sum(x is not None for x in (self.grams, self.raciones, self.meal)) != 1:
            raise ValueError("give exactly one of grams, raciones or meal")
        return self


class CarbsIn(CarbsPart):
    ts: Ts = None


class EntryUpdate(BaseModel):
    """Edit one logged event (the carbs and/or dose saved by one Log tap) as a whole.

    For each part: id and values -> update it; id without values -> delete it; values without id -> add it
    (with the event's time). All in one transaction. Removing everything is DELETE /api/entries."""

    ts: Annotated[AwareDatetime, AfterValidator(_check_ts)]
    carbs_id: int | None = None
    dose_id: int | None = None
    carbs: CarbsPart | None = None
    dose: DosePart | None = None

    @model_validator(mode="after")
    def _something_left(self):
        if self.carbs is None and self.dose is None:
            raise ValueError("an edit must keep carbs or a dose; to remove both, delete the entry")
        return self


class EntryIn(BaseModel):
    """One Log tap: carbs, a dose, or both, at the same time. Saved in one transaction."""

    ts: Ts = None
    carbs: CarbsPart | None = None
    dose: DosePart | None = None

    @model_validator(mode="after")
    def _something(self):
        if self.carbs is None and self.dose is None:
            raise ValueError("give carbs, a dose, or both")
        return self


class CarbsPatch(BaseModel):
    ts: Ts = None
    grams: Grams | None = None
    raciones: Raciones | None = None
    description: Description = None

    @model_validator(mode="after")
    def _at_most_one_amount(self):
        if self.grams is not None and self.raciones is not None:
            raise ValueError("give either grams or raciones, not both")
        for field in ("ts", "grams", "raciones"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be empty")
        return self


def _new_dose(conn, part: "DosePart", ts) -> dict:
    """Values for inserting a dose; with a calculator suggestion, its settings version and the suggestion."""
    values = {"ts": ts, **part.model_dump(exclude={"calc"})}
    if part.calc is not None:
        version = part.calc["settings_version"]
        if conn.execute("SELECT 1 FROM insulin_settings WHERE version = %s", (version,)).fetchone() is None:
            raise HTTPException(status_code=422, detail="calc refers to an unknown settings version")
        values["settings_version"] = version
        values["calc"] = Jsonb(part.calc)
    return values


def _racion_g(conn) -> Decimal | None:
    """The racion size from the current Settings > Insulin version; None if never saved."""
    s = insulin_settings.current(conn)
    return s["racion_g"] if s else None


NUTRIENTS = ("kcal", "carbs", "sugars", "fibre", "protein", "fat", "salt")


def _store_meal(conn, meal: "MealIn") -> tuple[int, Decimal]:
    """Store a meal with each food's name and values per 100 g as they are now. Returns (meal id, total
    available carbohydrate in g). A food without a carb value is refused: unknown carbs must never count as 0."""
    ids = [i.food_id for i in meal.items]
    foods = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, coalesce(own_name, name_en, name_fr) AS name, {', '.join(NUTRIENTS)} FROM food WHERE id = ANY(%s)", (ids,)
        ).fetchall()
    }
    unknown = [i for i in ids if i not in foods]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown food id: {unknown[0]}")
    no_carbs = [foods[i]["name"] for i in ids if foods[i]["carbs"] is None]
    if no_carbs:
        raise HTTPException(
            status_code=422, detail=f"no carbohydrate value for '{no_carbs[0]}': remove it or enter the carbs as grams"
        )
    total = sum((item.grams * foods[item.food_id]["carbs"] / 100 for item in meal.items), Decimal(0))
    total = total.quantize(Decimal("0.1"))
    if total > 1000:
        raise HTTPException(status_code=422, detail="more than 1000 g of carbs in one entry")
    meal_id = conn.execute("INSERT INTO meal DEFAULT VALUES RETURNING id").fetchone()["id"]
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO meal_item (meal_id, position, food_id, food_name, grams, {', '.join(NUTRIENTS)}) "
            f"VALUES (%s, %s, %s, %s, %s, {', '.join(['%s'] * len(NUTRIENTS))})",
            [
                (meal_id, pos, item.food_id, foods[item.food_id]["name"], item.grams, *[foods[item.food_id][n] for n in NUTRIENTS])
                for pos, item in enumerate(meal.items)
            ],
        )
    return meal_id, total


def _with_meals(conn, carbs: list[dict]) -> list[dict]:
    """Attach each meal entry's items and totals (from the values stored with the meal)."""
    ids = [c["meal_id"] for c in carbs if c.get("meal_id")]
    if not ids:
        return carbs
    items: dict[int, list] = {}
    for r in conn.execute(
        f"SELECT mi.meal_id, mi.food_id, mi.food_name, mi.grams, f.source, {', '.join('mi.' + n for n in NUTRIENTS)} "
        "FROM meal_item mi JOIN food f ON f.id = mi.food_id WHERE mi.meal_id = ANY(%s) ORDER BY mi.meal_id, mi.position",
        (ids,),
    ).fetchall():
        items.setdefault(r["meal_id"], []).append(r)
    for c in carbs:
        if not c.get("meal_id"):
            continue
        rows = items.get(c["meal_id"], [])
        totals, missing = {}, []
        for n in NUTRIENTS:
            values = [r["grams"] * r[n] / 100 for r in rows if r[n] is not None]
            totals[n] = float(round(sum(values, Decimal(0)), 1))
            if len(values) < len(rows):
                missing.append(n)
        c["meal"] = {
            "items": [
                {"food_id": r["food_id"], "name": r["food_name"], "source": r["source"], "grams": float(r["grams"]),
                 "carbs": float(round(r["grams"] * r["carbs"] / 100, 1)),
                 "per100": {n: (None if r[n] is None else float(r[n])) for n in NUTRIENTS}}
                for r in rows
            ],
            "totals": totals,
            "incomplete": missing,  # nutrients some item has no value for (their total is a lower bound)
        }
    return carbs


def _amount(conn, grams: Decimal | None, raciones: Decimal | None, meal: "MealIn | None" = None) -> dict:
    """Carbs as stored: always grams. A raciones entry converts with the saved racion size and keeps it,
    so a later change of that size never alters past entries. No size saved yet: grams only.
    A meal is stored with its items and stands for their total carbs."""
    if meal is not None:
        meal_id, total = _store_meal(conn, meal)
        return {"grams": total, "entry": "meal", "racion_g": None, "meal_id": meal_id}
    if raciones is not None:
        size = _racion_g(conn)
        if size is None:
            raise HTTPException(
                status_code=422, detail="racion size not set: save Settings > Insulin, or enter grams"
            )
        grams = raciones * size
        if grams > 1000:
            raise HTTPException(status_code=422, detail="more than 1000 g of carbs in one entry")
        return {"grams": grams, "entry": "raciones", "racion_g": size, "meal_id": None}
    return {"grams": grams, "entry": "grams", "racion_g": None, "meal_id": None}


DOSE_COLUMNS = sql.SQL(
    "id, floor(extract(epoch FROM ts))::bigint AS t, units, kind, insulin_name, note, settings_version"
)
CARB_COLUMNS = sql.SQL(
    "id, floor(extract(epoch FROM ts))::bigint AS t, grams, entry, racion_g, "
    "CASE WHEN entry = 'raciones' THEN round(grams / racion_g, 2) END AS raciones, description, meal_id"
)


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def _insert(table: str, values: dict, columns: sql.SQL, conn=None) -> dict:
    """Insert one row. A missing ts means "now", taken from the database clock (the transaction start,
    so rows inserted in one transaction share it). Pass `conn` to insert inside a caller's transaction."""
    values = dict(values)
    ts = values.pop("ts", None)
    names = list(values)
    query = sql.SQL("INSERT INTO {} ({}, ts) VALUES ({}, {}) RETURNING {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.SQL(", ").join(map(sql.Placeholder, names)),
        sql.SQL("now()") if ts is None else sql.Placeholder("ts"),
        columns,
    )
    if conn is not None:
        return conn.execute(query, {**values, "ts": ts}).fetchone()
    with _connect() as own:
        return own.execute(query, {**values, "ts": ts}).fetchone()


def _update_in(conn, table: str, row_id: int, values: dict, columns: sql.SQL) -> dict:
    """Update one live (not deleted) row inside the caller's transaction; 404 if there is none."""
    if not values:
        row = conn.execute(
            sql.SQL("SELECT {} FROM {} WHERE id = %s AND deleted_at IS NULL").format(columns, sql.Identifier(table)),
            (row_id,),
        ).fetchone()
    else:
        row = conn.execute(
            sql.SQL("UPDATE {} SET {} WHERE id = {} AND deleted_at IS NULL RETURNING {}").format(
                sql.Identifier(table),
                sql.SQL(", ").join(sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder(k)) for k in values),
                sql.Placeholder("_id"),
                columns,
            ),
            {**values, "_id": row_id},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return row


def _delete_in(conn, table: str, row_id: int) -> None:
    """Soft-delete one live row inside the caller's transaction; 404 if there is none."""
    row = conn.execute(
        sql.SQL("UPDATE {} SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL RETURNING id").format(
            sql.Identifier(table)
        ),
        (row_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="entry not found")


def _update(table: str, row_id: int, values: dict, columns: sql.SQL) -> dict:
    with _connect() as conn:
        return _update_in(conn, table, row_id, values, columns)


def _soft_delete(table: str, row_id: int) -> Response:
    with _connect() as conn:
        _delete_in(conn, table, row_id)
    return Response(status_code=204)


@router.get("/log")
def get_log(hours: int = Query(24, ge=1, le=168)):
    """Doses and carbs of the last `hours`, newest first, plus what the entry sheets pre-fill:
    the last insulin name per kind, how the last carbs were entered, and the racion size."""
    with _connect() as conn:
        doses = conn.execute(
            sql.SQL(
                "SELECT {} FROM insulin_dose WHERE deleted_at IS NULL "
                "AND ts >= now() - make_interval(hours => %s) ORDER BY ts DESC, id DESC"
            ).format(DOSE_COLUMNS),
            (hours,),
        ).fetchall()
        carbs = conn.execute(
            sql.SQL(
                "SELECT {} FROM carb_intake WHERE deleted_at IS NULL "
                "AND ts >= now() - make_interval(hours => %s) ORDER BY ts DESC, id DESC"
            ).format(CARB_COLUMNS),
            (hours,),
        ).fetchall()
        names = conn.execute(
            "SELECT DISTINCT ON (kind) kind, insulin_name FROM insulin_dose "
            "WHERE deleted_at IS NULL ORDER BY kind, ts DESC, id DESC"
        ).fetchall()
        last_entry = conn.execute(
            "SELECT entry FROM carb_intake WHERE deleted_at IS NULL ORDER BY ts DESC, id DESC LIMIT 1"
        ).fetchone()
        racion_g = _racion_g(conn)
        carbs = _with_meals(conn, carbs)
    last = {"rapid": None, "basal": None}
    last.update({r["kind"]: r["insulin_name"] for r in names})
    return {
        "doses": doses,
        "carbs": carbs,
        "last_insulin_name": last,
        "last_carb_entry": last_entry["entry"] if last_entry else None,
        # From Settings > Insulin; null until saved. The UI converts with it and never holds its own.
        "racion_g": racion_g,
    }


@router.post("/entries", status_code=201)
def add_entry(body: EntryIn):
    """Carbs and/or a dose from one Log tap, with one time, all or nothing."""
    with _connect() as conn:  # one transaction: commits on success, rolls back on any error
        carbs = dose = None
        if body.carbs is not None:
            c = body.carbs
            values = {"ts": body.ts, "description": c.description, **_amount(conn, c.grams, c.raciones, c.meal)}
            carbs = _insert("carb_intake", values, CARB_COLUMNS, conn)
        if body.dose is not None:
            dose = _insert("insulin_dose", _new_dose(conn, body.dose, body.ts), DOSE_COLUMNS, conn)
        if carbs is not None:
            carbs = _with_meals(conn, [carbs])[0]
        # Runs near this entry get their context (IOB, last meal, pre-run carbs) recomputed, same transaction.
        derive.recompute_near(conn, [body.ts])
    return {"carbs": carbs, "dose": dose}


def _times(conn, carbs_id: int | None, dose_id: int | None) -> list:
    """The current times of an event's rows (before an edit or delete moves or removes them)."""
    out = []
    if carbs_id is not None:
        out += [r["ts"] for r in conn.execute("SELECT ts FROM carb_intake WHERE id = %s", (carbs_id,))]
    if dose_id is not None:
        out += [r["ts"] for r in conn.execute("SELECT ts FROM insulin_dose WHERE id = %s", (dose_id,))]
    return out


@router.put("/entries")
def edit_entry(body: EntryUpdate):
    """Apply an edit of one event, all or nothing (any 404 or error rolls the whole edit back)."""
    with _connect() as conn:
        carbs = dose = None
        before = _times(conn, body.carbs_id, body.dose_id)
        if body.carbs is not None:
            c = body.carbs
            values = {"ts": body.ts, "description": c.description, **_amount(conn, c.grams, c.raciones, c.meal)}
            if body.carbs_id is not None:
                carbs = _update_in(conn, "carb_intake", body.carbs_id, values, CARB_COLUMNS)
            else:
                carbs = _insert("carb_intake", values, CARB_COLUMNS, conn)
        elif body.carbs_id is not None:
            _delete_in(conn, "carb_intake", body.carbs_id)
        if body.dose is not None:
            if body.dose_id is not None:
                # An edit never touches the suggestion the dose was logged with.
                values = {"ts": body.ts, **body.dose.model_dump(exclude={"calc"})}
                dose = _update_in(conn, "insulin_dose", body.dose_id, values, DOSE_COLUMNS)
            else:
                dose = _insert("insulin_dose", _new_dose(conn, body.dose, body.ts), DOSE_COLUMNS, conn)
        elif body.dose_id is not None:
            _delete_in(conn, "insulin_dose", body.dose_id)
        if carbs is not None:
            carbs = _with_meals(conn, [carbs])[0]
        derive.recompute_near(conn, [*before, body.ts])
    return {"carbs": carbs, "dose": dose}


@router.delete("/entries", status_code=204)
def delete_entry(carbs_id: int | None = None, dose_id: int | None = None):
    """Delete one event (its carbs and/or dose) in one transaction."""
    if carbs_id is None and dose_id is None:
        raise HTTPException(status_code=422, detail="give carbs_id, dose_id or both")
    with _connect() as conn:
        before = _times(conn, carbs_id, dose_id)
        if carbs_id is not None:
            _delete_in(conn, "carb_intake", carbs_id)
        if dose_id is not None:
            _delete_in(conn, "insulin_dose", dose_id)
        derive.recompute_near(conn, before)
    return Response(status_code=204)


@router.post("/doses", status_code=201)
def add_dose(body: DoseIn):
    with _connect() as conn:
        return _insert("insulin_dose", _new_dose(conn, body, body.ts), DOSE_COLUMNS, conn)


@router.patch("/doses/{dose_id}")
def edit_dose(dose_id: int, body: DosePatch):
    return _update("insulin_dose", dose_id, body.model_dump(exclude_unset=True), DOSE_COLUMNS)


@router.delete("/doses/{dose_id}", status_code=204)
def delete_dose(dose_id: int):
    return _soft_delete("insulin_dose", dose_id)


@router.post("/carbs", status_code=201)
def add_carbs(body: CarbsIn):
    with _connect() as conn:
        values = {"ts": body.ts, "description": body.description, **_amount(conn, body.grams, body.raciones, body.meal)}
        return _with_meals(conn, [_insert("carb_intake", values, CARB_COLUMNS, conn)])[0]


@router.patch("/carbs/{carb_id}")
def edit_carbs(carb_id: int, body: CarbsPatch):
    sent = body.model_dump(exclude_unset=True)
    values = {k: sent[k] for k in ("ts", "description") if k in sent}
    with _connect() as conn:
        if "grams" in sent or "raciones" in sent:
            values.update(_amount(conn, sent.get("grams"), sent.get("raciones")))
        return _update_in(conn, "carb_intake", carb_id, values, CARB_COLUMNS)


@router.delete("/carbs/{carb_id}", status_code=204)
def delete_carbs(carb_id: int):
    return _soft_delete("carb_intake", carb_id)

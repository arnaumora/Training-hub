"""Settings > Insulin (roadmap step 4): the only place insulin parameters come from.

- Every save is a new version (migration 0004); the newest is the current one. Old versions are kept
  and can never be changed, so any calculation can be reproduced from the version it used.
- Nothing has a default value. Until every field is saved there is no current version, and the
  calculator stays off (it lists what is missing).
- Values outside the usual ranges below are not refused, but saving them needs a second, explicit
  confirmation (confirm_unusual). The ranges are plausibility guards, never values the app uses.
- Neither the app nor the AI ever writes here: only this endpoint, from the settings screen.
"""

import json
import re
from decimal import Decimal
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from .common import conninfo, require_auth

router = APIRouter(prefix="/api/settings/insulin", dependencies=[Depends(require_auth)])

# Usual ranges for an adult on pens. Outside them, saving needs a second confirmation.
USUAL = {
    "ratio": (Decimal("0.3"), Decimal("4"), "units per racion"),
    "isf": (Decimal("15"), Decimal("120"), "mg/dL per unit"),
    "correct_above": (100, 200, "mg/dL"),
    "correct_to": (80, 180, "mg/dL"),
    "action_time_h": (Decimal("3"), Decimal("6"), "hours"),
    "racion_g": (Decimal("10"), Decimal("12"), "g"),
}

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _strip(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("cannot be empty")
    return value


class MealSlot(BaseModel):
    name: Annotated[str, Field(max_length=30), AfterValidator(_strip)]
    start: str  # "HH:MM", local time (Europe/Madrid)
    ratio: Annotated[Decimal, Field(gt=0, le=20, decimal_places=2)]  # units per racion

    @field_validator("start")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not HHMM.match(v):
            raise ValueError("start must be HH:MM")
        return v


class SettingsIn(BaseModel):
    meal_slots: Annotated[list[MealSlot], Field(min_length=1, max_length=8)]
    isf: Annotated[Decimal, Field(gt=0, le=500, decimal_places=1)]
    correct_above: Annotated[int, Field(ge=40, le=400)]
    correct_to: Annotated[int, Field(ge=40, le=400)]
    action_time_h: Annotated[Decimal, Field(gt=0, le=12, decimal_places=1)]
    action_time_provisional: bool
    pen_increment: Decimal  # units the pen dials in steps of: 0.5 or 1
    racion_g: Annotated[Decimal, Field(gt=0, le=50, decimal_places=1)]
    rapid_insulin: Annotated[str, Field(max_length=40), AfterValidator(_strip)]
    note: Annotated[str | None, Field(max_length=200)] = None
    confirm_unusual: bool = False

    @field_validator("pen_increment")
    @classmethod
    def _pen(cls, v: Decimal) -> Decimal:
        if v not in (Decimal("0.5"), Decimal("1")):
            raise ValueError("pen increment must be 0.5 or 1")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        if self.correct_to > self.correct_above:
            raise ValueError("'correct down to' cannot be above 'correct above'")
        names = [s.name.lower() for s in self.meal_slots]
        if len(set(names)) != len(names):
            raise ValueError("meal slot names must be different")
        starts = [s.start for s in self.meal_slots]
        if len(set(starts)) != len(starts):
            raise ValueError("meal slots cannot start at the same time")
        return self

    def unusual(self) -> list[dict]:
        """Fields outside USUAL, for the second confirmation."""
        out = []

        def check(field: str, label: str, value) -> None:
            lo, hi, unit = USUAL[field]
            if not lo <= value <= hi:
                out.append({"field": label, "value": str(value), "usual": f"{lo} to {hi} {unit}"})

        for s in self.meal_slots:
            check("ratio", f"{s.name} ratio", s.ratio)
        for field in ("isf", "correct_above", "correct_to", "action_time_h", "racion_g"):
            check(field, field, getattr(self, field))
        return out


COLUMNS = (
    "version, floor(extract(epoch FROM created_at))::bigint AS saved_at, note, meal_slots, isf, correct_above, "
    "correct_to, action_time_h, action_time_provisional, pen_increment, racion_g, rapid_insulin"
)


def current(conn) -> dict | None:
    """The newest saved version, or None when Settings > Insulin was never saved."""
    return conn.execute(f"SELECT {COLUMNS} FROM insulin_settings ORDER BY version DESC LIMIT 1").fetchone()


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def _usual_for_ui() -> dict:
    return {k: {"min": lo, "max": hi, "unit": unit} for k, (lo, hi, unit) in USUAL.items()}


@router.get("")
def get_settings():
    with _connect() as conn:
        return {"current": current(conn), "usual": _usual_for_ui()}


@router.get("/history")
def get_history():
    with _connect() as conn:
        return conn.execute(f"SELECT {COLUMNS} FROM insulin_settings ORDER BY version DESC LIMIT 100").fetchall()


@router.post("", status_code=201)
def save_settings(body: SettingsIn):
    unusual = body.unusual()
    if unusual and not body.confirm_unusual:
        return JSONResponse(
            status_code=409,
            content={"detail": "some values are outside the usual range; confirm them to save", "unusual": unusual},
        )
    slots = sorted(
        ({"name": s.name, "start": s.start, "ratio": float(s.ratio)} for s in body.meal_slots), key=lambda s: s["start"]
    )
    note = (body.note or "").strip() or None
    with _connect() as conn:
        return conn.execute(
            f"""
            INSERT INTO insulin_settings (note, meal_slots, isf, correct_above, correct_to, action_time_h,
                action_time_provisional, pen_increment, racion_g, rapid_insulin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {COLUMNS}
            """,
            (
                note,
                json.dumps(slots),
                body.isf,
                body.correct_above,
                body.correct_to,
                body.action_time_h,
                body.action_time_provisional,
                body.pen_increment,
                body.racion_g,
                body.rapid_insulin,
            ),
        ).fetchone()

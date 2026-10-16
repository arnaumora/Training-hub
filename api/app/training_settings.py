"""Settings > Training (roadmap step 7): heart-rate zones, the glucose target range and the sensor lag offset;
from step 9 (change 3) also the pace zones, shown as the targets of planned runs.

Versioned and append-only (migration 0013): every save is a new version, the newest is current. Version 1
holds the run log's starting values. Per-run metrics store the version they used.

GET  /api/settings/training   {"current": {...}, "history": [...newest first], "pace_zone_names": {...}}
POST /api/settings/training   save a new version (all fields; validated below)
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from .common import conninfo, require_auth

router = APIRouter(prefix="/api/settings/training", dependencies=[Depends(require_auth)])

COLUMNS = "version, created_at, note, lthr, max_hr, resting_hr, hr_zone_floors, glucose_low, glucose_high, lag_min, pace_zones"

# Pace zones (step 9, change 3), seconds per km, [fastest, slowest]. The calendar shows them as the targets of
# planned runs (app.plan_calendar.pace_zone_of). A zone left out has no target.
PACE_ZONES = {"easy": "Easy", "long": "Long run", "steady": "Steady", "tempo": "Tempo", "threshold": "Threshold",
              "race": "Half marathon pace"}


class TrainingIn(BaseModel):
    lthr: Annotated[int, Field(ge=100, le=230)]
    max_hr: Annotated[int, Field(ge=100, le=240)]
    resting_hr: Annotated[int, Field(ge=25, le=110)]
    hr_zone_floors: Annotated[list[Annotated[int, Field(ge=60, le=240)]], Field(min_length=4, max_length=4)]
    glucose_low: Annotated[int, Field(ge=50, le=110)]
    glucose_high: Annotated[int, Field(ge=120, le=300)]
    lag_min: Annotated[int, Field(ge=0, le=30)]
    pace_zones: dict[str, Annotated[list[Annotated[int, Field(ge=150, le=900)]], Field(min_length=2, max_length=2)]] = {}
    note: Annotated[str | None, Field(max_length=200)] = None

    @model_validator(mode="after")
    def _consistent(self):
        f = self.hr_zone_floors
        if any(b <= a for a, b in zip(f, f[1:])):
            raise ValueError("each zone must start above the one before it")
        if not self.resting_hr < self.lthr <= self.max_hr:
            raise ValueError("resting HR must be below LTHR, and LTHR at most max HR")
        if f[-1] > self.max_hr:
            raise ValueError("zone 5 cannot start above max HR")
        if f[0] <= self.resting_hr:
            raise ValueError("zone 2 must start above resting HR")
        for k, (a, b) in self.pace_zones.items():
            if k not in PACE_ZONES:
                raise ValueError(f"unknown pace zone: {k}")
            if a >= b:
                raise ValueError("a pace zone goes from the faster pace to the slower one")
        return self


def row(r: dict) -> dict:
    return {**r, "created_at": r["created_at"].timestamp(), "hr_zone_floors": list(r["hr_zone_floors"]),
            "pace_zones": r["pace_zones"] or {}}


def current(conn) -> dict | None:
    """The newest version (any connection; returns plain dicts)."""
    with conn.cursor(row_factory=dict_row) as cur:
        r = cur.execute(f"SELECT {COLUMNS} FROM training_settings ORDER BY version DESC LIMIT 1").fetchone()
    return row(r) if r else None


@router.get("")
def get_training_settings():
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(f"SELECT {COLUMNS} FROM training_settings ORDER BY version DESC").fetchall()
    history = [row(r) for r in rows]
    return {"current": history[0] if history else None, "history": history, "pace_zone_names": PACE_ZONES}


@router.post("", status_code=201)
def save_training_settings(body: TrainingIn):
    note = (body.note or "").strip() or None
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        cur = current(conn)
        fields = body.model_dump(exclude={"note"})
        if cur and all(cur[k] == v for k, v in fields.items()):
            raise HTTPException(status_code=409, detail="nothing changed")
        r = conn.execute(
            f"""INSERT INTO training_settings (note, lthr, max_hr, resting_hr, hr_zone_floors, glucose_low, glucose_high, lag_min, pace_zones)
                VALUES (%(note)s, %(lthr)s, %(max_hr)s, %(resting_hr)s, %(hr_zone_floors)s, %(glucose_low)s, %(glucose_high)s, %(lag_min)s,
                        %(pace_zones)s)
                RETURNING {COLUMNS}""",
            {**fields, "note": note, "pace_zones": Jsonb(fields["pace_zones"]) if fields["pace_zones"] else None},
        ).fetchone()
    return row(r)

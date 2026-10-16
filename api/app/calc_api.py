"""POST /api/calculate: the calculator's suggestion for the Log form, recomputed as you type.

Gathers the inputs from the database (current Settings > Insulin version, newest glucose reading,
rapid doses still within the action time, first rapid dose ever logged) and hands them to the pure
calculator in calculator.py. Nothing is stored here; a suggestion is stored only with the dose it
was logged with (POST /api/entries, dose.calc).
"""

from datetime import timedelta
from decimal import Decimal

import psycopg
from fastapi import APIRouter, Depends
from psycopg.rows import dict_row
from pydantic import BaseModel, model_validator

from . import insulin_settings
from .calculator import Dose, Glucose, calculate
from .common import conninfo, require_auth
from .log import Grams, Raciones

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class CalcIn(BaseModel):
    grams: Grams | None = None
    raciones: Raciones | None = None

    @model_validator(mode="after")
    def _at_most_one(self):
        if self.grams is not None and self.raciones is not None:
            raise ValueError("give grams or raciones, not both")
        return self


@router.post("/calculate")
def suggest(body: CalcIn):
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        settings = insulin_settings.current(conn)
        if settings is None:
            return {"available": False, "reason": "Save Settings > Insulin to use the calculator."}
        now = conn.execute("SELECT now() AS now").fetchone()["now"]
        g = conn.execute("SELECT ts, mg_dl, trend FROM glucose_reading ORDER BY minute DESC LIMIT 1").fetchone()
        window = timedelta(hours=float(settings["action_time_h"]))
        doses = conn.execute(
            "SELECT ts, units FROM insulin_dose WHERE kind = 'rapid' AND deleted_at IS NULL "
            "AND ts > %s AND ts <= %s",
            (now - window, now + timedelta(minutes=5)),
        ).fetchall()
        first = conn.execute(
            "SELECT min(ts) AS first FROM insulin_dose WHERE kind = 'rapid' AND deleted_at IS NULL"
        ).fetchone()["first"]

    carbs_g = body.grams
    if body.raciones is not None:
        carbs_g = body.raciones * Decimal(settings["racion_g"])
    result = calculate(
        settings,
        now,
        carbs_g,
        None if g is None else Glucose(ts=g["ts"], mg_dl=g["mg_dl"], trend=g["trend"]),
        [Dose(ts=d["ts"], units=d["units"]) for d in doses],
        first,
    )
    return {"available": True, "result": result.as_dict()}

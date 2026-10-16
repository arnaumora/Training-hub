"""Cross-run comparison (roadmap step 7, changes 3 and 4).

GET /api/compare?sport=running&days=90&min_km=5&max_km=10
  Every activity in a filter with its stored numbers (app.derive) and its glucose as change from the start,
  for the overlay, the scatter and the table.
    sport          one sport, or all when left out
    days           activities that started in the last N days, or all when left out
    min_km/max_km  distance range, either end optional
  Returns:
    sports   every sport with its activity count, unfiltered (for the filter's options)
    runs     oldest first, each with its numbers and `curve`: [[minutes from the start, mg/dL change from the
             start], ...] from the start to CURVE_AFTER_S after the end. The curve starts at [0, 0] by
             definition (glucose_start is the reading nearest the start), and is empty without a
             glucose_start. Those runs are still listed, so the screen can say how many it could not draw.

GET /api/compare/similar?sport=running&glucose=150&trend=3&iob=1.5&since_meal=90&k=5
  The past activities whose starting state was closest to the one given (app.similar), and what happened.
  Any of the four may be left out; at least one is needed.

GET /api/compare/now
  The starting state right now, to fill the similar-runs form and the Now screen: the latest glucose and trend
  (if under NOW_MAX_AGE_S old), IOB from the calculator's insulin_on_board, and minutes since the last carbs
  and since the last rapid dose. The same
  definitions as the per-run context (app.run_metrics.context), with the start being now.

History only: nothing here computes, suggests or changes a dose.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from . import derive, similar, training_settings
from . import run_metrics as rm
from .common import conninfo, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

CURVE_AFTER_S = 120 * 60  # same window as the run page: post-run drops matter as much as the run
NOW_MAX_AGE_S = 15 * 60  # an older reading is not "now"

NUMBERS = ("glucose_start", "glucose_end", "delta", "rate_per_10min", "nadir", "nadir_minute", "min_below_low",
           "coverage_pct", "low_coverage", "readings", "log_covered", "iob_at_start", "min_since_last_meal",
           "pre_run_carbs", "trend_start", "hr_drift_pct", "grade_adjusted_pace_s", "avg_pace_s")


def _num(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    return float(v)


def _runs(conn, sport=None, days=None, min_km=None, max_km=None) -> list[dict]:
    """Activities in the filter, oldest first, with their stored numbers as plain JSON values."""
    rows = conn.execute(
        """SELECT a.id, a.sport, a.sub_sport, a.start_ts, a.end_ts, a.elapsed_s, a.moving_s, a.distance_m, a.avg_hr,
                  g.glucose_start, g.glucose_end, g.delta, g.rate_per_10min, g.nadir, g.nadir_minute,
                  g.min_below_low, g.coverage_pct, g.low_coverage, g.readings,
                  c.log_covered, c.iob_at_start, c.min_since_last_meal, c.pre_run_carbs, c.trend_start,
                  c.local_time, t.hr_drift_pct, t.grade_adjusted_pace_s, t.avg_pace_s
           FROM activity a
           LEFT JOIN run_glucose_metrics g ON g.activity_id = a.id
           LEFT JOIN run_context c ON c.activity_id = a.id
           LEFT JOIN run_training_metrics t ON t.activity_id = a.id
           WHERE (%(sport)s::text IS NULL OR a.sport = %(sport)s)
             AND (%(days)s::int IS NULL OR a.start_ts >= now() - make_interval(days => %(days)s))
             AND (%(min_m)s::numeric IS NULL OR a.distance_m >= %(min_m)s)
             AND (%(max_m)s::numeric IS NULL OR a.distance_m <= %(max_m)s)
           ORDER BY a.start_ts""",
        {"sport": sport, "days": days,
         "min_m": None if min_km is None else min_km * 1000, "max_m": None if max_km is None else max_km * 1000},
    ).fetchall()
    out = []
    for r in rows:
        lt = r["local_time"]
        out.append({
            "id": r["id"], "sport": r["sport"], "sub_sport": r["sub_sport"],
            "start": r["start_ts"].timestamp(), "end": r["end_ts"].timestamp(),
            "elapsed_s": _num(r["elapsed_s"]), "moving_s": _num(r["moving_s"]), "distance_m": _num(r["distance_m"]),
            "avg_hr": r["avg_hr"],
            **{k: _num(r[k]) for k in NUMBERS},
            # Time of day as hours (7.5 = 07:30) for colouring and the scatter; the text for reading.
            "local_hour": None if lt is None else round(lt.hour + lt.minute / 60, 2),
            "local_time": None if lt is None else lt.strftime("%H:%M"),
        })
    return out


@router.get("/compare")
def compare(
    sport: Annotated[str | None, Query(max_length=40)] = None,
    days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    min_km: Annotated[float | None, Query(ge=0, le=1000)] = None,
    max_km: Annotated[float | None, Query(ge=0, le=1000)] = None,
):
    derive.refresh()  # stale rows first (missing, older settings, recent runs): normally none
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        sports = conn.execute(
            "SELECT sport, count(*) AS n FROM activity GROUP BY sport ORDER BY count(*) DESC, sport"
        ).fetchall()
        runs = _runs(conn, sport, days, min_km, max_km)
        ids = [r["id"] for r in runs if r["glucose_start"] is not None]
        points = conn.execute(
            """SELECT a.id, round((extract(epoch FROM gr.ts - a.start_ts) / 60)::numeric, 1)::float8 AS m, gr.mg_dl
               FROM activity a
               JOIN glucose_reading gr ON gr.minute > a.start_ts AND gr.minute <= a.end_ts + %s
               WHERE a.id = ANY(%s)
               ORDER BY a.id, gr.ts""",
            (timedelta(seconds=CURVE_AFTER_S), ids),
        ).fetchall() if ids else []

    curves: dict[int, list] = {}
    for p in points:
        curves.setdefault(p["id"], []).append((p["m"], p["mg_dl"]))
    for r in runs:
        start = r["glucose_start"]
        r["curve"] = [[0, 0]] + [[m, v - start] for m, v in curves.get(r["id"], []) if m > 0] if start is not None else []
    return {"sports": sports, "runs": runs}


@router.get("/compare/similar")
def similar_runs(
    sport: Annotated[str | None, Query(max_length=40)] = None,
    glucose: Annotated[int | None, Query(ge=40, le=500)] = None,
    trend: Annotated[int | None, Query(ge=1, le=5)] = None,
    iob: Annotated[float | None, Query(ge=0, le=50)] = None,
    since_meal: Annotated[int | None, Query(ge=0, le=7 * 24 * 60)] = None,
    k: Annotated[int, Query(ge=1, le=10)] = 5,
    before: Annotated[int | None, Query(ge=0)] = None,
):
    """before (epoch seconds): only activities that started earlier, as the run page asks for one run."""
    state = {"glucose_start": glucose, "trend_start": trend, "iob_at_start": iob, "min_since_last_meal": since_meal}
    if all(v is None for v in state.values()):
        raise HTTPException(status_code=422, detail="give at least one of glucose, trend, IOB or time since the last meal")
    derive.refresh()
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        runs = _runs(conn, sport)
        ts = training_settings.current(conn)
    if before is not None:
        runs = [r for r in runs if r["start"] < before]
    # The low end of the target (Settings > Training), for "went below 70".
    return {**similar.rank(runs, state, k), "low": ts["glucose_low"] if ts else None}


@router.get("/compare/now")
def now_state():
    now = datetime.now(UTC)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        g = conn.execute("SELECT ts, mg_dl, trend, source = 'llu_live' AS live FROM glucose_reading ORDER BY minute DESC LIMIT 1").fetchone()
        log_started = conn.execute(
            "SELECT least((SELECT min(ts) FROM insulin_dose), (SELECT min(ts) FROM carb_intake)) AS t"
        ).fetchone()["t"]
        doses = conn.execute(
            "SELECT ts, units, kind FROM insulin_dose WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s",
            (now - derive.CONTEXT_BEFORE, now),
        ).fetchall()
        carbs = conn.execute(
            "SELECT ts, grams FROM carb_intake WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s",
            (now - derive.CONTEXT_BEFORE, now),
        ).fetchall()
        ins = derive._insulin_at(conn, now)
    c = rm.context(
        now, now,
        rapid=[d for d in doses if d["kind"] == "rapid"],
        basal=[d for d in doses if d["kind"] == "basal"],
        carbs=carbs,
        action_time_h=ins["action_time_h"] if ins else None,
        log_started=log_started,
    )
    age = None if g is None else round((now - g["ts"]).total_seconds() / 60)
    fresh = g is not None and age * 60 <= NOW_MAX_AGE_S
    return {
        "glucose": g["mg_dl"] if fresh else None,
        "trend": g["trend"] if fresh and g["live"] else None,
        "glucose_age_min": age,
        "iob": _num(c["iob_at_start"]),
        "min_since_last_meal": c["min_since_last_meal"],
        "min_since_last_bolus": c["min_since_last_bolus"],
        "log_covered": c["log_covered"],
    }

"""What the Claude desktop connector reads (roadmap step 8, change 3), in the shapes that suit a model: compact,
local times, no 1 s streams. Read-only like everything a token can reach (app.common.require_auth).

GET /api/connector/reference                 the reference text the AI reports use (app/ai_guidelines.md)
GET /api/connector/activity/{id}             one activity as the run report sees it (app.ai_input.build_run),
                                             with its flags and its newest report
GET /api/connector/glucose?start=&end=       readings between two times (at most 14 days) and their figures
                                             (app.week.glucose_stats); spans over 12 h come as 15-min buckets
GET /api/connector/log?start=&end=           doses and carbs between two times (at most 62 days), meals with
                                             their foods
Times in: ISO 8601 (a bare date is local midnight). Times out: local, "YYYY-MM-DD HH:MM".
The other tools use the app's own endpoints: /api/compare, /api/compare/similar, /api/compare/now,
/api/weeks/{day}, /api/weeks/{day}/report, /api/settings/training.
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from . import ai_input, derive, training_settings, week
from .common import conninfo, require_auth
from .log import _with_meals

router = APIRouter(prefix="/api/connector", dependencies=[Depends(require_auth)])

LOCAL = ZoneInfo("Europe/Madrid")
GLUCOSE_MAX = timedelta(days=14)
LOG_MAX = timedelta(days=62)
RAW_UP_TO = timedelta(hours=12)
BUCKET = timedelta(minutes=15)


def _when(raw: str) -> datetime:
    """ISO 8601 in; a time without a zone, or a bare date, is local time."""
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time(0), LOCAL)
        t = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"not a date or time: {raw}") from None
    return t if t.tzinfo else t.replace(tzinfo=LOCAL)


def _span(start: str, end: str, limit: timedelta) -> tuple[datetime, datetime]:
    a, b = _when(start), _when(end)
    if b <= a:
        raise HTTPException(status_code=422, detail="end must be after start")
    if b - a > limit:
        raise HTTPException(status_code=422, detail=f"at most {limit.days} days at a time")
    return a, b


def _local(t: datetime) -> str:
    return t.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M")


@router.get("/reference")
def reference():
    return {"text": (Path(__file__).with_name("ai_guidelines.md")).read_text(encoding="utf-8")}


@router.get("/activity/{activity_id}")
def activity(activity_id: int):
    derive.refresh(activity_id)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        built = ai_input.build_run(conn, activity_id)
        if built is None:
            raise HTTPException(status_code=404, detail="activity not found")
        r = conn.execute(
            "SELECT created_at, provider, model, text FROM ai_report WHERE scope = 'activity' AND ref_id = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (activity_id,),
        ).fetchone()
    data, _ = built
    data["id"] = activity_id
    data["report"] = None if r is None else {"written": _local(r["created_at"]), "by": f"{r['provider']} {r['model']}", "text": r["text"]}
    return data


@router.get("/glucose")
def glucose(start: Annotated[str, Query(max_length=40)], end: Annotated[str, Query(max_length=40)]):
    a, b = _span(start, end, GLUCOSE_MAX)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT ts, mg_dl, source = 'llu_live' AS live FROM glucose_reading WHERE minute >= %s AND minute < %s ORDER BY minute",
            (a, b),
        ).fetchall()
        ts = training_settings.current(conn)
    low, high = (ts["glucose_low"], ts["glucose_high"]) if ts else (70, 180)
    readings = [(r["ts"], r["mg_dl"], r["live"]) for r in rows]
    out = {"start": _local(a), "end": _local(b), "figures": week.glucose_stats(readings, a, min(b, datetime.now(LOCAL)), low, high)}
    if b - a <= RAW_UP_TO:
        out["columns"] = ["time", "mg_dl", "live"]
        out["readings"] = [[_local(t), mg, 1 if live else 0] for t, mg, live in readings]
    else:
        buckets: dict[datetime, list[int]] = {}
        for t, mg, _ in readings:
            key = a + ((t - a) // BUCKET) * BUCKET
            buckets.setdefault(key, []).append(mg)
        out["columns"] = ["time (15-min bucket)", "mean", "min", "max", "readings"]
        out["readings"] = [[_local(k), round(sum(v) / len(v)), min(v), max(v), len(v)] for k, v in sorted(buckets.items())]
    return out


@router.get("/log")
def log(start: Annotated[str, Query(max_length=40)], end: Annotated[str, Query(max_length=40)]):
    a, b = _span(start, end, LOG_MAX)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        doses = conn.execute(
            "SELECT ts, units, kind, insulin_name, note FROM insulin_dose WHERE deleted_at IS NULL AND ts >= %s AND ts < %s ORDER BY ts",
            (a, b),
        ).fetchall()
        carbs = _with_meals(conn, conn.execute(
            "SELECT id, ts, grams, description, meal_id FROM carb_intake WHERE deleted_at IS NULL AND ts >= %s AND ts < %s ORDER BY ts",
            (a, b),
        ).fetchall())
        first = conn.execute("SELECT least((SELECT min(ts) FROM insulin_dose), (SELECT min(ts) FROM carb_intake)) AS t").fetchone()["t"]
    entries = sorted(
        [{"time": _local(d["ts"]), "insulin_u": float(d["units"]), "kind": d["kind"], "insulin": d["insulin_name"],
          **({"note": d["note"]} if d["note"] else {})} for d in doses]
        + [{"time": _local(c["ts"]), "carbs_g": float(c["grams"]), **({"what": c["description"]} if c["description"] else {}),
            **({"foods": [f"{i['name']} {i['grams']:g} g" for i in c["meal"]["items"]]} if c.get("meal") else {})}
           for c in carbs],
        key=lambda e: e["time"],
    )
    return {"start": _local(a), "end": _local(b), "log_started": None if first is None else _local(first), "entries": entries}

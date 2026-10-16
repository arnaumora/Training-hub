"""Activities: manual FIT upload and the activity list (roadmap step 6).

POST /api/activities/fit   the file's bytes as the request body (a .fit, or Garmin Connect's export .zip).
                           Raw body instead of a multipart form: no extra dependency, and the phone sends
                           the file as it is. Archived raw, parsed and stored by app.fit_import.
GET  /api/activities       newest first, the summary of each (one per FIT session).
GET  /api/activities/{id}  one activity with its laps.
GET  /api/activities/{id}/graph  everything the run page draws: the summary and laps, the 1 s records as
                           columns, glucose from GLUCOSE_BEFORE_S before the start to GLUCOSE_AFTER_S after
                           the end, and the doses and carbs logged in that window (same shape as /api/log).
"""

import logging
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from psycopg import sql
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from . import ai_input, derive, fit_import, training_settings
from .common import conninfo, require_auth
from .log import CARB_COLUMNS, DOSE_COLUMNS, _with_meals

log = logging.getLogger("traininghub.activities")

# The glucose window around an activity (spec: 60 min before to 120 min after, because post-run drops
# matter as much as the run).
GLUCOSE_BEFORE_S = 60 * 60
GLUCOSE_AFTER_S = 120 * 60
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

SUMMARY = """
    SELECT id, source, sport, sub_sport, start_ts, end_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr,
           ascent_m, descent_m, calories, avg_speed_mps, device, garmin_activity_id
    FROM activity
"""


def _f(v):
    return None if v is None else float(v)


def summary(r: dict) -> dict:
    return {
        "id": r["id"],
        "source": r["source"],
        "sport": r["sport"],
        "sub_sport": r["sub_sport"],
        "start": r["start_ts"].timestamp(),
        "end": r["end_ts"].timestamp(),
        "elapsed_s": _f(r["elapsed_s"]),
        "moving_s": _f(r["moving_s"]),
        "distance_m": _f(r["distance_m"]),
        "avg_hr": r["avg_hr"],
        "max_hr": r["max_hr"],
        "ascent_m": _f(r["ascent_m"]),
        "descent_m": _f(r["descent_m"]),
        "calories": r["calories"],
        "avg_speed_mps": _f(r["avg_speed_mps"]),
        "device": r["device"],
        "garmin_activity_id": r["garmin_activity_id"],
    }


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


@router.post("/activities/fit", status_code=201)
async def upload_fit(request: Request):
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > fit_import.MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 25 MB")
    data = await request.body()

    def work():  # parsing a long activity takes a moment: off the event loop
        with _connect() as conn:
            result = fit_import.import_bytes(conn, data, source="upload")
            rows = conn.execute(f"{SUMMARY} WHERE id = ANY(%s) ORDER BY start_ts", (result["ids"],)).fetchall()
        return result, rows

    try:
        result, rows = await run_in_threadpool(work)
    except fit_import.FitProblem as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except OSError as e:
        log.error("could not archive an uploaded FIT file: %s", e)
        raise HTTPException(status_code=503, detail="the raw archive is not writable: nothing was imported") from e
    return {"duplicate": result["duplicate"], "activities": [summary(r) for r in rows]}


@router.get("/activities")
def list_activities(limit: Annotated[int, Query(ge=1, le=200)] = 50):
    with _connect() as conn:
        rows = conn.execute(f"{SUMMARY} ORDER BY start_ts DESC, session_index LIMIT %s", (limit,)).fetchall()
    return [summary(r) for r in rows]


@router.get("/activities/{activity_id}")
def get_activity(activity_id: int):
    with _connect() as conn:
        r = conn.execute(f"{SUMMARY} WHERE id = %s", (activity_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="activity not found")
        laps = conn.execute(
            "SELECT idx, start_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr, avg_speed_mps "
            "FROM activity_lap WHERE activity_id = %s ORDER BY idx",
            (activity_id,),
        ).fetchall()
        n = conn.execute("SELECT count(*) AS n FROM activity_record WHERE activity_id = %s", (activity_id,)).fetchone()["n"]
    out = summary(r)
    out["laps"] = [
        {"idx": x["idx"], "start": x["start_ts"].timestamp(), "elapsed_s": _f(x["elapsed_s"]), "moving_s": _f(x["moving_s"]),
         "distance_m": _f(x["distance_m"]), "avg_hr": x["avg_hr"], "max_hr": x["max_hr"], "avg_speed_mps": _f(x["avg_speed_mps"])}
        for x in laps
    ]
    out["records"] = n
    return out


RECORD_COLUMNS = ("distance_m", "hr", "speed_mps", "altitude_m", "cadence")


def _metrics(conn, activity_id: int) -> dict:
    """The stored per-run numbers (app.derive) and the note, as plain JSON values."""
    def one(sql):
        r = conn.execute(sql, (activity_id,)).fetchone()
        if r is None:
            return None
        r = dict(r)
        r.pop("activity_id", None)
        for k, v in list(r.items()):
            if hasattr(v, "timestamp"):
                r[k] = v.timestamp()
            elif hasattr(v, "isoformat"):  # a time of day
                r[k] = v.strftime("%H:%M")
            elif v is not None and not isinstance(v, (int, bool, str, list, dict)):
                r[k] = float(v)
        return r

    note = conn.execute("SELECT note FROM activity_note WHERE activity_id = %s", (activity_id,)).fetchone()
    return {
        "context": one("SELECT * FROM run_context WHERE activity_id = %s"),
        "glucose": one("SELECT * FROM run_glucose_metrics WHERE activity_id = %s"),
        "training": one("SELECT * FROM run_training_metrics WHERE activity_id = %s"),
        "note": note["note"] if note else None,
    }


@router.get("/activities/{activity_id}/graph")
def activity_graph(activity_id: int):
    # Fresh numbers when they are missing, out of date or the run is recent (its glucose may still arrive).
    derive.refresh(activity_id)
    with _connect() as conn:
        r = conn.execute(f"{SUMMARY} WHERE id = %s", (activity_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="activity not found")
        laps = conn.execute(
            "SELECT idx, start_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr, avg_speed_mps "
            "FROM activity_lap WHERE activity_id = %s ORDER BY idx",
            (activity_id,),
        ).fetchall()
        # One row of arrays: the records as columns, in time order (smaller and faster than row objects).
        cols = conn.execute(
            "SELECT coalesce(array_agg(extract(epoch FROM ts)::bigint ORDER BY ts), '{}') AS t, "
            + ", ".join(f"coalesce(array_agg({c}::float8 ORDER BY ts), '{{}}') AS {c}" for c in RECORD_COLUMNS)
            + " FROM activity_record WHERE activity_id = %s",
            (activity_id,),
        ).fetchone()
        start, end = r["start_ts"].timestamp(), r["end_ts"].timestamp()
        lo, hi = int(start) - GLUCOSE_BEFORE_S, int(end) + GLUCOSE_AFTER_S
        glucose = conn.execute(
            "SELECT extract(epoch FROM minute)::bigint AS t, mg_dl, source = 'llu_live' AS live FROM glucose_reading "
            "WHERE minute >= to_timestamp(%s) AND minute <= to_timestamp(%s) ORDER BY minute",
            (lo, hi),
        ).fetchall()
        doses = conn.execute(
            sql.SQL(
                "SELECT {} FROM insulin_dose WHERE deleted_at IS NULL AND ts >= to_timestamp(%s) AND ts <= to_timestamp(%s) ORDER BY ts"
            ).format(DOSE_COLUMNS),
            (lo, hi),
        ).fetchall()
        s = training_settings.current(conn)
        carbs = _with_meals(conn, conn.execute(
            sql.SQL(
                "SELECT {} FROM carb_intake WHERE deleted_at IS NULL AND ts >= to_timestamp(%s) AND ts <= to_timestamp(%s) ORDER BY ts"
            ).format(CARB_COLUMNS),
            (lo, hi),
        ).fetchall())
    out = summary(r)
    out["laps"] = [
        {"idx": x["idx"], "start": x["start_ts"].timestamp(), "elapsed_s": _f(x["elapsed_s"]), "moving_s": _f(x["moving_s"]),
         "distance_m": _f(x["distance_m"]), "avg_hr": x["avg_hr"], "max_hr": x["max_hr"], "avg_speed_mps": _f(x["avg_speed_mps"])}
        for x in laps
    ]
    out["window"] = {"from": lo, "to": hi}
    out["records"] = {"t": cols["t"], **{c: cols[c] for c in RECORD_COLUMNS}}
    out["glucose"] = [[g["t"], g["mg_dl"], g["live"]] for g in glucose]
    out["log"] = {"doses": doses, "carbs": carbs}
    # Zones, target range and lag offset for drawing (Settings > Training, current version).
    out["settings"] = s
    with _connect() as conn:
        out["metrics"] = _metrics(conn, activity_id)
    # Safety and data flags, decided by code (app.run_metrics.flags); the AI report gets the same ones.
    m = out["metrics"]
    out["flags"] = ai_input.run_flags(
        m["glucose"], m["context"], [((g["t"] - start) / 60, g["mg_dl"]) for g in glucose], r["start_ts"], r["end_ts"],
        float(r["moving_s"] or r["elapsed_s"]), s["glucose_low"] if s else 70,
    )
    return out


class NoteIn(BaseModel):
    note: Annotated[str, Field(max_length=2000)]


@router.put("/activities/{activity_id}/note")
def set_note(activity_id: int, body: NoteIn):
    """Your note on an activity; an empty note removes it. Kept when the numbers are recomputed."""
    text = body.note.strip()
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM activity WHERE id = %s", (activity_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="activity not found")
        if text:
            conn.execute(
                "INSERT INTO activity_note (activity_id, note) VALUES (%s, %s) "
                "ON CONFLICT (activity_id) DO UPDATE SET note = EXCLUDED.note, updated_at = now()",
                (activity_id, text),
            )
        else:
            conn.execute("DELETE FROM activity_note WHERE activity_id = %s", (activity_id,))
    return {"note": text or None}

"""Derived per-run numbers (roadmap step 7): load the inputs, call app.run_metrics, store one row per table.

Idempotent: compute(activity) replaces its three rows whole. When it runs:
  - right after an activity is imported (upload or Garmin sync: app.fit_import.import_bytes)
  - right after the Log changes an entry (app.log: add, edit, delete), for the activities near it
  - when a run page is opened and its numbers are missing, out of date or the run is recent (app.activities)
  - every REFRESH_EVERY_S in the garmin service: rows that are missing, computed with an older Settings >
    Training version, or for activities that ended in the last RECENT_H hours (their glucose may still be
    arriving: LibreLinkUp backfills up to 12 h of history)
  - by hand: python -m app.derive --all   (rebuild everything, for example after changing a formula)
"""

import sys
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import run_metrics as rm
from . import training_settings
from .common import conninfo

RECENT_H = 14
REFRESH_EVERY_S = 10 * 60
CONTEXT_BEFORE = timedelta(hours=48)  # the longest look-back of the context (last basal)


def _insulin_at(conn, start):
    """The Settings > Insulin version current at `start`; before the first one, the first one (the action time
    is a property of the insulin, so the first saved value is the best one known for earlier runs)."""
    r = conn.execute(
        "SELECT version, action_time_h FROM insulin_settings WHERE created_at <= %s ORDER BY version DESC LIMIT 1", (start,)
    ).fetchone()
    return r or conn.execute("SELECT version, action_time_h FROM insulin_settings ORDER BY version LIMIT 1").fetchone()


def compute(conn, activity_id: int) -> None:
    """Recompute and store the three rows of one activity (conn with dict_row; caller commits)."""
    a = conn.execute("SELECT id, sport, start_ts, end_ts FROM activity WHERE id = %s", (activity_id,)).fetchone()
    if a is None:
        return
    start, end = a["start_ts"], a["end_ts"]
    ts = training_settings.current(conn)
    low = ts["glucose_low"] if ts else 70
    ins = _insulin_at(conn, start)

    readings = conn.execute(
        "SELECT ts, mg_dl, trend, source = 'llu_live' AS live FROM glucose_reading WHERE ts BETWEEN %s AND %s",
        (start - timedelta(minutes=15), end + timedelta(seconds=rm.AFTER_S + 15 * 60)),
    ).fetchall()
    g = rm.glucose_metrics(readings, start, end, low)

    log_started = conn.execute(
        "SELECT least((SELECT min(ts) FROM insulin_dose), (SELECT min(ts) FROM carb_intake)) AS t"
    ).fetchone()["t"]
    doses = conn.execute(
        "SELECT ts, units, kind FROM insulin_dose WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s",
        (start - CONTEXT_BEFORE, end),
    ).fetchall()
    carbs = conn.execute(
        "SELECT ts, grams FROM carb_intake WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s", (start - CONTEXT_BEFORE, end)
    ).fetchall()
    c = rm.context(
        start, end,
        rapid=[d for d in doses if d["kind"] == "rapid"],
        basal=[d for d in doses if d["kind"] == "basal"],
        carbs=carbs,
        action_time_h=ins["action_time_h"] if ins else None,
        log_started=log_started,
    )

    rec = conn.execute(
        "SELECT coalesce(array_agg(extract(epoch FROM ts)::bigint ORDER BY ts), '{}') AS t, "
        "coalesce(array_agg(hr ORDER BY ts), '{}') AS hr, coalesce(array_agg(speed_mps::float8 ORDER BY ts), '{}') AS speed, "
        "coalesce(array_agg(distance_m::float8 ORDER BY ts), '{}') AS dist, "
        "coalesce(array_agg(altitude_m::float8 ORDER BY ts), '{}') AS alt FROM activity_record WHERE activity_id = %s",
        (activity_id,),
    ).fetchone()
    has_hr = any(h is not None for h in rec["hr"])
    tiz = rm.time_in_zone(rec["t"], rec["hr"], ts["hr_zone_floors"]) if ts and has_hr else None
    drift = rm.hr_drift_pct(rec["t"], rec["hr"], rec["speed"]) if has_hr else None
    gap = rm.grade_adjusted_pace(a["sport"], rec["t"], rec["dist"], rec["alt"], rec["speed"])
    version = ts["version"] if ts else None

    for table in ("run_context", "run_glucose_metrics", "run_training_metrics"):
        conn.execute(f"DELETE FROM {table} WHERE activity_id = %s", (activity_id,))
    conn.execute(
        """INSERT INTO run_context (activity_id, insulin_settings_version, log_covered, iob_at_start, min_since_last_bolus,
               last_bolus_units, last_meal_carbs, min_since_last_meal, pre_run_carbs, glucose_start, trend_start,
               h_since_basal, local_time)
           VALUES (%(id)s, %(iv)s, %(log_covered)s, %(iob_at_start)s, %(min_since_last_bolus)s, %(last_bolus_units)s,
               %(last_meal_carbs)s, %(min_since_last_meal)s, %(pre_run_carbs)s, %(gs)s, %(gt)s, %(h_since_basal)s, %(local_time)s)""",
        {**c, "id": activity_id, "iv": ins["version"] if ins else None, "gs": g["glucose_start"], "gt": g["trend_start"]},
    )
    conn.execute(
        """INSERT INTO run_glucose_metrics (activity_id, training_settings_version, glucose_start, glucose_end, nadir,
               nadir_minute, delta, rate_per_10min, min_below_low, coverage_pct, low_coverage, readings)
           VALUES (%(id)s, %(v)s, %(glucose_start)s, %(glucose_end)s, %(nadir)s, %(nadir_minute)s, %(delta)s,
               %(rate_per_10min)s, %(min_below_low)s, %(coverage_pct)s, %(low_coverage)s, %(readings)s)""",
        {**g, "id": activity_id, "v": version},
    )
    conn.execute(
        """INSERT INTO run_training_metrics (activity_id, training_settings_version, time_in_zone, hr_drift_pct,
               avg_pace_s, grade_adjusted_pace_s)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (activity_id, version, Jsonb(tiz) if tiz is not None else None, drift, gap["avg_pace_s"], gap["grade_adjusted_pace_s"]),
    )


def recompute(conn, ids) -> int:
    for i in ids:
        compute(conn, i)
    return len(ids)


def near(conn, times) -> list[int]:
    """Activities whose context can change when a log entry at one of `times` changes: the entry is in the 48 h
    before the start, or between the start and the end."""
    times = [t for t in times if t is not None]
    if not times:
        return []
    rows = conn.execute(
        "SELECT DISTINCT a.id FROM activity a, unnest(%s::timestamptz[]) t WHERE t BETWEEN a.start_ts - %s AND a.end_ts",
        (times, CONTEXT_BEFORE),
    ).fetchall()
    return [r["id"] for r in rows]


def recompute_near(conn, times) -> int:
    """After a Log change: recompute the activities near it, in the caller's transaction (dict_row conn)."""
    return recompute(conn, near(conn, times))


def stale(conn, activity_id: int | None = None) -> list[int]:
    """Activities whose numbers are missing, from an older training settings version, or recent."""
    ts = training_settings.current(conn)
    version = ts["version"] if ts else None
    rows = conn.execute(
        """SELECT a.id FROM activity a
           LEFT JOIN run_glucose_metrics g ON g.activity_id = a.id
           LEFT JOIN run_training_metrics t ON t.activity_id = a.id
           LEFT JOIN run_context c ON c.activity_id = a.id
           WHERE (%(id)s::bigint IS NULL OR a.id = %(id)s)
             AND (g.activity_id IS NULL OR t.activity_id IS NULL OR c.activity_id IS NULL
                  OR g.training_settings_version IS DISTINCT FROM %(v)s
                  OR t.training_settings_version IS DISTINCT FROM %(v)s
                  OR a.end_ts > now() - make_interval(hours => %(h)s))
           ORDER BY a.start_ts""",
        {"id": activity_id, "v": version, "h": RECENT_H},
    ).fetchall()
    return [r["id"] for r in rows]


def refresh(activity_id: int | None = None) -> int:
    """Recompute what is stale (one activity, or all). Own connection and transaction."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        return recompute(conn, stale(conn, activity_id))


def main(argv: list[str]) -> int:
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if argv == ["--all"]:
            ids = [r["id"] for r in conn.execute("SELECT id FROM activity ORDER BY start_ts").fetchall()]
        elif argv == ["--stale"]:
            ids = stale(conn)
        else:
            print("usage: python -m app.derive --all | --stale")
            return 2
        n = recompute(conn, ids)
    print(f"recomputed {n} activities")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

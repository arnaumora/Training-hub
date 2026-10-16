"""What the model is given for a run report (roadmap step 8, spec "AI integration"): the numbers code computed,
the context, the zones, what was logged, the 5 most similar past runs and their outcomes, and downsampled
series (glucose as recorded, at most 1 per minute; heart rate, pace and altitude in 30 s means). Never the raw
1 s streams.

Times are minutes from the start of the activity (negative before it), plus the local start time once, so
the model reasons in the same frame as the run page.

build_run(conn, activity_id) -> (input dict, input_sha)
  input_sha hashes everything except the similar runs, which change whenever a new run arrives: a report
  is out of date when its own run's data changed, not when the history grew.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import compare, similar, training_settings
from . import run_metrics as rm
from .log import _with_meals

LOCAL = ZoneInfo("Europe/Madrid")
SERIES_S = 30
LOG_BEFORE = timedelta(hours=6)
GLUCOSE_BEFORE = timedelta(minutes=60)
GLUCOSE_AFTER = timedelta(minutes=120)
TREND = {1: "falling fast", 2: "falling", 3: "stable", 4: "rising", 5: "rising fast"}


def _f(v, places=1):
    return None if v is None else round(float(v), places)


def _pace(sport, speed_mps):
    if not speed_mps or speed_mps < 1.0:
        return None
    if sport == "cycling":
        return round(speed_mps * 3.6, 1)  # km/h
    return round(1000 / speed_mps)  # s/km


def _mmss(s):
    if s is None:
        return None
    r = round(float(s))
    return f"{r // 60}:{r % 60:02d}"


def run_flags(g: dict | None, c: dict | None, points: list, start, end, moving_s: float, low: int) -> list[dict]:
    """app.run_metrics.flags from the stored numbers; points as (minutes from the start, mg/dL)."""
    g, c = g or {}, c or {}
    return rm.flags(
        glucose_start=g.get("glucose_start"), points=points, duration_min=(end - start).total_seconds() / 60,
        nadir=g.get("nadir"), nadir_minute=g.get("nadir_minute"), min_below_low=g.get("min_below_low") or 0, low=low,
        coverage_pct=g.get("coverage_pct"), readings=g.get("readings") or 0, log_covered=c.get("log_covered"),
        min_since_last_bolus=c.get("min_since_last_bolus"),
        end_local_hour=(lambda t: t.hour + t.minute / 60)(end.astimezone(LOCAL)), moving_min=moving_s / 60,
    )


def build_run(conn, activity_id: int) -> tuple[dict, str] | None:
    """conn with dict_row. None if the activity does not exist."""
    a = conn.execute(
        "SELECT id, sport, sub_sport, start_ts, end_ts, elapsed_s, moving_s, distance_m, avg_hr, max_hr, ascent_m, "
        "descent_m, calories, device FROM activity WHERE id = %s",
        (activity_id,),
    ).fetchone()
    if a is None:
        return None
    start, end = a["start_ts"], a["end_ts"]
    rel = lambda t: round((t - start).total_seconds() / 60, 1)  # noqa: E731
    sport = a["sport"]
    ride = sport == "cycling"
    moving = float(a["moving_s"] or a["elapsed_s"])

    activity = {
        "sport": sport + (f" ({a['sub_sport']})" if a["sub_sport"] and a["sub_sport"] != "generic" else ""),
        "start_local": start.astimezone(LOCAL).strftime("%Y-%m-%d %H:%M (%a)"),
        "elapsed_min": _f(float(a["elapsed_s"]) / 60), "moving_min": _f(moving / 60),
        "distance_km": _f(float(a["distance_m"]) / 1000, 2) if a["distance_m"] else None,
        "avg_hr": a["avg_hr"], "max_hr": a["max_hr"], "ascent_m": _f(a["ascent_m"], 0), "descent_m": _f(a["descent_m"], 0),
        "calories": a["calories"],
    }
    if a["distance_m"] and sport != "swimming":
        activity["avg_speed_kmh" if ride else "avg_pace_min_km"] = (
            _f(float(a["distance_m"]) / moving * 3.6) if ride else _mmss(moving / float(a["distance_m"]) * 1000)
        )

    laps = [
        {"lap": r["idx"] + 1, "km": _f(float(r["distance_m"] or 0) / 1000, 2), "min": _f(float(r["moving_s"] or r["elapsed_s"]) / 60),
         "avg_hr": r["avg_hr"], "max_hr": r["max_hr"],
         ("speed_kmh" if ride else "pace_min_km"): (
             _f(float(r["avg_speed_mps"]) * 3.6) if ride and r["avg_speed_mps"]
             else _mmss(1000 / float(r["avg_speed_mps"])) if r["avg_speed_mps"] else None)}
        for r in conn.execute(
            "SELECT idx, distance_m, moving_s, elapsed_s, avg_hr, max_hr, avg_speed_mps FROM activity_lap WHERE activity_id = %s ORDER BY idx",
            (activity_id,),
        ).fetchall()
    ]

    ts = training_settings.current(conn)
    settings = None
    if ts:
        f = ts["hr_zone_floors"]
        settings = {
            "lthr": ts["lthr"], "max_hr": ts["max_hr"], "resting_hr": ts["resting_hr"],
            "hr_zones_bpm": {"z1": f"< {f[0]}", "z2": f"{f[0]}-{f[1] - 1}", "z3": f"{f[1]}-{f[2] - 1}",
                             "z4": f"{f[2]}-{f[3] - 1}", "z5": f">= {f[3]}"},
            "glucose_target_mg_dl": [ts["glucose_low"], ts["glucose_high"]],
            "sensor_lag_min": ts["lag_min"],
        }

    def one(sql):
        r = conn.execute(sql, (activity_id,)).fetchone()
        if r is None:
            return None
        return {k: (_f(v, 2) if v is not None and not isinstance(v, (bool, int, str, list, dict)) and not hasattr(v, "isoformat") else v)
                for k, v in r.items() if k not in ("activity_id", "computed_at", "local_time")}

    g = one("SELECT * FROM run_glucose_metrics WHERE activity_id = %s")
    c = one("SELECT * FROM run_context WHERE activity_id = %s")
    t = one("SELECT * FROM run_training_metrics WHERE activity_id = %s")
    # The starting state for finding similar runs, before the trend becomes words.
    state = {k: (c or {}).get(k) for k in ("glucose_start", "trend_start", "iob_at_start", "min_since_last_meal")}
    if c and c.get("trend_start"):
        c["trend_start"] = TREND.get(c["trend_start"], c["trend_start"])
    if t and t.get("time_in_zone"):
        t["time_in_zone_min"] = {f"z{i + 1}": round(s / 60, 1) for i, s in enumerate(t.pop("time_in_zone"))}
    for k in ("avg_pace_s", "grade_adjusted_pace_s"):
        if t and t.get(k) is not None:
            t[k.replace("_s", "_min_km")] = _mmss(t.pop(k))
    note = conn.execute("SELECT note FROM activity_note WHERE activity_id = %s", (activity_id,)).fetchone()

    doses = conn.execute(
        "SELECT ts, units, kind, insulin_name, note FROM insulin_dose WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s ORDER BY ts",
        (start - LOG_BEFORE, end + GLUCOSE_AFTER),
    ).fetchall()
    carbs = _with_meals(conn, conn.execute(
        "SELECT id, ts, grams, description, meal_id FROM carb_intake WHERE deleted_at IS NULL AND ts BETWEEN %s AND %s ORDER BY ts",
        (start - LOG_BEFORE, end + GLUCOSE_AFTER),
    ).fetchall())
    log = sorted(
        [{"min": rel(d["ts"]), "insulin_u": _f(d["units"]), "kind": d["kind"], "insulin": d["insulin_name"],
          **({"note": d["note"]} if d["note"] else {})} for d in doses]
        + [{"min": rel(x["ts"]), "carbs_g": _f(x["grams"]),
            **({"what": x["description"]} if x["description"] else {}),
            **({"foods": [f"{i['name']} {i['grams']:g} g" for i in x["meal"]["items"]]} if x.get("meal") else {})}
           for x in carbs],
        key=lambda e: e["min"],
    )

    glucose = conn.execute(
        "SELECT ts, mg_dl, source = 'llu_live' AS live FROM glucose_reading WHERE minute BETWEEN %s AND %s ORDER BY minute",
        (start - GLUCOSE_BEFORE, end + GLUCOSE_AFTER),
    ).fetchall()
    rec = conn.execute(
        f"""SELECT floor(elapsed_s / {SERIES_S}) AS b, avg(hr)::float8 AS hr, avg(speed_mps)::float8 AS speed,
                   avg(altitude_m)::float8 AS alt
            FROM activity_record WHERE activity_id = %s GROUP BY 1 ORDER BY 1""",
        (activity_id,),
    ).fetchall()
    gps = sport not in ("swimming", "training") and any(r["speed"] for r in rec)
    columns = ["min", "hr"] + ((["speed_kmh"] if ride else ["pace_s_km"]) + ["alt_m"] if gps else [])
    rows = []
    for r in rec:
        row = [round(float(r["b"]) * SERIES_S / 60, 1), None if r["hr"] is None else round(r["hr"])]
        if gps:
            row += [_pace(sport, r["speed"]), None if r["alt"] is None else round(r["alt"])]
        rows.append(row)

    # What the report must not leave out, decided by code (the run page shows the same).
    flag_list = run_flags(g, c, [(rel(x["ts"]), x["mg_dl"]) for x in glucose], start, end, moving,
                          ts["glucose_low"] if ts else 70)

    data = {
        "flags_must_mention": [f["text"] for f in flag_list],
        "activity": activity,
        "laps": laps if len(laps) > 1 else [],
        "settings": settings,
        "glucose_metrics": g,
        "context_before": c,
        "training_metrics": t,
        "note": note["note"] if note else None,
        "log_6h_before_to_2h_after": log,
        "glucose_series": {"columns": ["min", "mg_dl", "live"], "rows": [[rel(x["ts"]), x["mg_dl"], 1 if x["live"] else 0] for x in glucose]},
        "series_30s": {"columns": columns, "rows": rows},
    }
    sha = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()

    # The 5 past runs that started most like this one (app.similar), and what happened in them.
    past = [r for r in compare._runs(conn, sport) if r["start"] < start.timestamp()]
    sim = similar.rank(past, state, 5) if any(v is not None for v in state.values()) else None
    data["similar_past_runs"] = None if sim is None else {
        "compared_on": sim["used"],
        "n": sim["summary"]["n"],
        "summary": sim["summary"],
        "runs": [
            {k: r.get(k) for k in ("glucose_start", "trend_start", "iob_at_start", "min_since_last_meal", "pre_run_carbs",
                                   "delta", "rate_per_10min", "nadir", "nadir_minute", "min_below_low", "low_coverage", "local_time",
                                   "avg_hr")}
            | {"date": _date(r["start"]), "minutes": round((r["moving_s"] or r["elapsed_s"]) / 60), "km": _f((r["distance_m"] or 0) / 1000, 1)}
            for r in sim["runs"]
        ],
    }
    return data, sha


def _date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone(LOCAL).strftime("%Y-%m-%d")

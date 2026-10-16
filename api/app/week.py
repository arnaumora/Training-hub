"""One training week, Monday to Sunday in Europe/Madrid (roadmap step 8, change 2): the numbers the Week
screen shows and the weekly AI summary is given. Code computes every number; the model only interprets.

GET /api/weeks/{monday}   (monday as YYYY-MM-DD; any date in the week works, it is moved back to the Monday)

  sessions     every activity that started in the week, with its stored numbers and its flags
  by_sport     count, minutes and km per sport
  zones_min    minutes per heart-rate zone, all sessions together
  glucose      the week's readings, time-weighted (glucose_stats): in range, below the low, below 54, above
               the high, above 250, mean, and coverage (share of the week's minutes with a reading)
  nights       for each day with a session, the night after it (22:00 to 07:00): minutes below the low
  days         per day: sessions, carbs, rapid and basal insulin logged, entries, time in range
  previous     the week before, in brief: sessions, minutes, time in range and below the low
  flags        what the summary must mention (week_flags)

Time in range targets from the international consensus on time in range (Battelino et al., Diabetes Care
2019;42:1593-1603): over 70 % in 70-180 mg/dL, under 4 % below 70, under 1 % below 54, under 25 % above
180, under 5 % above 250, read with at least 70 % of the time covered by the sensor.
"""

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from . import ai_input, compare, derive, training_settings
from .common import conninfo, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

LOCAL = ZoneInfo("Europe/Madrid")
LIVE_S = 60
HISTORY_S = 15 * 60
VERY_LOW = 54
VERY_HIGH = 250
NIGHT_FROM = time(22, 0)
NIGHT_TO = time(7, 0)
TARGET_TIR = 70
TARGET_BELOW = 4
TARGET_VERY_HIGH = 5
MIN_COVERAGE = 70


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def bounds(monday: date) -> tuple[datetime, datetime]:
    """The week as UTC instants: Monday 00:00 to the next Monday 00:00, local time (DST-safe)."""
    start = datetime.combine(monday, time(0), LOCAL)
    end = datetime.combine(monday + timedelta(days=7), time(0), LOCAL)
    return start.astimezone(UTC), end.astimezone(UTC)


# --- pure ---------------------------------------------------------------------------------------------


def glucose_stats(readings: list[tuple], start: datetime, end: datetime, low: int, high: int) -> dict:
    """readings: (ts, mg_dl, live) in time order. Each reading stands for the time until the next one, at most
    1 min (live) or 15 min (history), and never past `end`. Shares are of the covered time."""
    covered = {"in": 0.0, "below": 0.0, "very_low": 0.0, "above": 0.0, "very_high": 0.0}
    total = 0.0
    weighted = 0.0
    for i, (ts, mg, live) in enumerate(readings):
        if ts < start or ts >= end:
            continue
        nxt = readings[i + 1][0] if i + 1 < len(readings) else end
        span = max(0.0, min((LIVE_S if live else HISTORY_S), (min(nxt, end) - ts).total_seconds()))
        total += span
        weighted += mg * span
        if mg < VERY_LOW:
            covered["very_low"] += span
        if mg < low:
            covered["below"] += span
        elif mg <= high:
            covered["in"] += span
        else:
            covered["above"] += span
        if mg > VERY_HIGH:
            covered["very_high"] += span
    window = (end - start).total_seconds()
    pct = lambda s: round(100 * s / total, 1) if total else None  # noqa: E731
    return {
        "coverage_pct": round(100 * total / window, 1) if window > 0 else 0.0,
        "in_range_pct": pct(covered["in"]),
        "below_pct": pct(covered["below"]),
        "very_low_pct": pct(covered["very_low"]),
        "above_pct": pct(covered["above"]),
        "very_high_pct": pct(covered["very_high"]),
        "mean": round(weighted / total) if total else None,
        "low": low,
        "high": high,
    }


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def week_flags(sessions: list[dict], glucose: dict, nights: list[dict], days: list[dict], low: int,
               glucose_from: str | None = None, log_from: str | None = None) -> list[dict]:
    """What the week's summary must mention. Kinds as in app.run_metrics.flags.
    glucose_from / log_from: the day glucose collection or the log started, when that was during this week;
    "never" when it had not started by the end of the week. Weeks before the app had data are not gaps."""
    out = []

    def add(kind, code, text):
        out.append({"kind": kind, "code": code, "text": text})

    high_sessions = [s for s in sessions if any("above 270" in f["text"] for f in s["flags"])]
    if high_sessions:
        add("safety", "ketones", f"{_n(len(high_sessions), 'session')} with glucose above 270 mg/dL ({', '.join(s['day'] for s in high_sessions)}): "
                      "the exercise consensus advises checking ketones at that level when the high is unexplained.")
    low_sessions = [s for s in sessions if s.get("nadir") is not None and s["nadir"] < low]
    if low_sessions:
        add("safety", "went_low", f"Below {low} mg/dL during or in the 2 h after {_n(len(low_sessions), 'session')} "
                      f"({', '.join(s['day'] for s in low_sessions)}).")
    night_lows = [n for n in nights if n["min_below"]]
    if night_lows:
        add("safety", "night_low", f"Below {low} mg/dL overnight after training: "
                      + "; ".join(f"night after {n['day']}, {n['min_below']} min, lowest {n['lowest']}" for n in night_lows) + ".")
    if glucose["below_pct"] is not None and glucose["below_pct"] >= TARGET_BELOW:
        add("safety", "time_low", f"Time below {low} was {glucose['below_pct']:g} % of the covered time (the consensus target is under {TARGET_BELOW} %).")
    if glucose["very_high_pct"] is not None and glucose["very_high_pct"] >= TARGET_VERY_HIGH:
        add("safety", "time_very_high", f"Time above {VERY_HIGH} was {glucose['very_high_pct']:g} % of the covered time (the consensus target is under {TARGET_VERY_HIGH} %).")
    if log_from == "never":
        add("log", "log_never", "The insulin and carbs log had not started yet this week: nothing is known about insulin or carbs.")
    else:
        if log_from:
            add("log", "log_start", f"The insulin and carbs log started on {log_from}: insulin and carbs before it are unknown.")
        no_rapid = [d["day"] for d in days if d["rapid_u"] == 0 and not d["future"] and not d.get("before_log")]
        if no_rapid:
            add("log", "no_bolus", f"No rapid insulin logged on {', '.join(no_rapid)}: insulin figures for those days are incomplete if doses went unlogged.")
    if glucose_from == "never":
        add("data", "glucose_never", "Glucose collection had not started yet this week: there are no glucose figures.")
    else:
        if glucose_from:
            add("data", "glucose_start", f"Glucose collection started on {glucose_from}: the glucose figures count from then.")
        if glucose["coverage_pct"] < MIN_COVERAGE:
            add("data", "coverage", f"The sensor covered {glucose['coverage_pct']:g} % of the time: under the {MIN_COVERAGE} % the consensus asks "
                        "for before time-in-range figures are read as reliable.")
    return out


# --- from the database --------------------------------------------------------------------------------


def _readings(conn, start, end):
    return [(r["ts"], r["mg_dl"], r["live"]) for r in conn.execute(
        "SELECT ts, mg_dl, source = 'llu_live' AS live FROM glucose_reading WHERE minute >= %s AND minute < %s ORDER BY minute",
        (start, end),
    ).fetchall()]


def numbers(conn, monday: date) -> dict:
    """Everything the Week screen shows and the summary is given (conn with dict_row)."""
    start, end = bounds(monday)
    now = datetime.now(UTC)
    upto = min(end, now)
    ts = training_settings.current(conn)
    low, high = (ts["glucose_low"], ts["glucose_high"]) if ts else (70, 180)

    readings = _readings(conn, start - timedelta(hours=2), end + timedelta(hours=12))
    in_week = [r for r in readings if start <= r[0] < end]
    # When glucose collection and the log began: before that is not a gap, there was no app yet.
    first = conn.execute(
        "SELECT (SELECT min(minute) FROM glucose_reading) AS glucose, "
        "least((SELECT min(ts) FROM insulin_dose), (SELECT min(ts) FROM carb_intake)) AS log"
    ).fetchone()
    g_from = first["glucose"] if first["glucose"] is not None and first["glucose"] < end else None
    g_start = max(start, g_from) if g_from else start
    glucose = (glucose_stats(in_week, g_start, upto, low, high) if g_from and upto > g_start
               else glucose_stats([], start, start, low, high))
    glucose_from = "never" if g_from is None else (g_from.astimezone(LOCAL).strftime("%a %d %b") if g_from > start else None)
    log_start = first["log"]
    log_from = ("never" if log_start is None or log_start >= end
                else log_start.astimezone(LOCAL).strftime("%a %d %b") if log_start > start else None)

    sessions = []
    for r in compare._runs(conn):
        st = datetime.fromtimestamp(r["start"], UTC)
        if not (start <= st < end):
            continue
        en = datetime.fromtimestamp(r["end"], UTC)
        points = [((t - st).total_seconds() / 60, mg) for t, mg, _ in readings if st - timedelta(hours=1) <= t <= en + timedelta(hours=2)]
        g = {k: r[k] for k in ("glucose_start", "nadir", "nadir_minute", "min_below_low", "coverage_pct", "readings")}
        c = {k: r[k] for k in ("log_covered",)}
        c["min_since_last_bolus"] = conn.execute(
            "SELECT min_since_last_bolus FROM run_context WHERE activity_id = %s", (r["id"],)
        ).fetchone()
        c["min_since_last_bolus"] = c["min_since_last_bolus"]["min_since_last_bolus"] if c["min_since_last_bolus"] else None
        flags = ai_input.run_flags(g, c, points, st, en, (r["moving_s"] or r["elapsed_s"]), low)
        local = st.astimezone(LOCAL)
        sessions.append({
            "id": r["id"], "sport": r["sport"], "sub_sport": r["sub_sport"], "start": r["start"],
            "day": local.strftime("%a %d %b"), "local_time": local.strftime("%H:%M"),
            "minutes": round((r["moving_s"] or r["elapsed_s"]) / 60), "km": None if not r["distance_m"] else round(r["distance_m"] / 1000, 2),
            "avg_hr": r["avg_hr"],
            **{k: r[k] for k in ("glucose_start", "glucose_end", "delta", "rate_per_10min", "nadir", "nadir_minute",
                                 "min_below_low", "coverage_pct", "low_coverage", "iob_at_start", "min_since_last_meal",
                                 "pre_run_carbs", "hr_drift_pct", "log_covered")},
            "flags": flags,
        })

    by_sport: dict[str, dict] = {}
    for s in sessions:
        b = by_sport.setdefault(s["sport"], {"sport": s["sport"], "n": 0, "minutes": 0, "km": 0.0})
        b["n"] += 1
        b["minutes"] += s["minutes"]
        b["km"] = round(b["km"] + (s["km"] or 0), 2)
    ids = [s["id"] for s in sessions]
    zones = [0.0] * 5
    for row in conn.execute(
        "SELECT time_in_zone FROM run_training_metrics WHERE activity_id = ANY(%s) AND time_in_zone IS NOT NULL", (ids,)
    ).fetchall() if ids else []:
        for i, sec in enumerate(row["time_in_zone"]):
            zones[i] += sec / 60

    nights = []
    for d in sorted({datetime.fromtimestamp(s["start"], UTC).astimezone(LOCAL).date() for s in sessions}):
        n0 = datetime.combine(d, NIGHT_FROM, LOCAL).astimezone(UTC)
        n1 = datetime.combine(d + timedelta(days=1), NIGHT_TO, LOCAL).astimezone(UTC)
        if n0 >= now:
            continue
        night = [r for r in readings if n0 <= r[0] < n1]
        stats = glucose_stats(night, n0, min(n1, now), low, high)
        covered_min = stats["coverage_pct"] * (min(n1, now) - n0).total_seconds() / 6000
        nights.append({
            "day": d.strftime("%a %d %b"),
            "lowest": min((r[1] for r in night), default=None),
            "min_below": round(covered_min * (stats["below_pct"] or 0) / 100),
            "coverage_pct": stats["coverage_pct"],
        })

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        d0 = datetime.combine(d, time(0), LOCAL).astimezone(UTC)
        d1 = datetime.combine(d + timedelta(days=1), time(0), LOCAL).astimezone(UTC)
        log = conn.execute(
            """SELECT
                 (SELECT coalesce(sum(grams), 0) FROM carb_intake WHERE deleted_at IS NULL AND ts >= %(a)s AND ts < %(b)s) AS carbs,
                 (SELECT coalesce(sum(units), 0) FROM insulin_dose WHERE deleted_at IS NULL AND kind = 'rapid' AND ts >= %(a)s AND ts < %(b)s) AS rapid,
                 (SELECT coalesce(sum(units), 0) FROM insulin_dose WHERE deleted_at IS NULL AND kind = 'basal' AND ts >= %(a)s AND ts < %(b)s) AS basal,
                 (SELECT count(*) FROM carb_intake WHERE deleted_at IS NULL AND ts >= %(a)s AND ts < %(b)s)
                 + (SELECT count(*) FROM insulin_dose WHERE deleted_at IS NULL AND ts >= %(a)s AND ts < %(b)s) AS entries""",
            {"a": d0, "b": d1},
        ).fetchone()
        future = d0 >= now
        tir = glucose_stats([r for r in readings if d0 <= r[0] < d1], d0, min(d1, now), low, high) if not future else None
        days.append({
            "date": d.isoformat(), "day": d.strftime("%a %d %b"), "future": future,
            "before_log": log_start is None or d1 <= log_start,
            "sessions": sum(1 for s in sessions if s["day"] == d.strftime("%a %d %b")),
            "carbs_g": float(log["carbs"]), "rapid_u": float(log["rapid"]), "basal_u": float(log["basal"]),
            "entries": log["entries"],
            "in_range_pct": tir["in_range_pct"] if tir else None, "coverage_pct": tir["coverage_pct"] if tir else None,
        })

    p0, p1 = bounds(monday - timedelta(days=7))
    prev_sessions = conn.execute(
        "SELECT count(*) AS n, coalesce(sum(coalesce(moving_s, elapsed_s)), 0) AS s FROM activity WHERE start_ts >= %s AND start_ts < %s",
        (p0, p1),
    ).fetchone()
    prev_g = glucose_stats(_readings(conn, p0, p1), p0, p1, low, high)

    return {
        "monday": monday.isoformat(),
        "label": f"Week of {monday.strftime('%a %d %b %Y')}",
        "complete": end <= now,
        "sessions": sessions,
        "by_sport": sorted(by_sport.values(), key=lambda b: -b["minutes"]),
        "zones_min": [round(z, 1) for z in zones],
        "glucose": glucose,
        "nights": nights,
        "days": days,
        "previous": {"sessions": prev_sessions["n"], "minutes": round(float(prev_sessions["s"]) / 60),
                     "in_range_pct": prev_g["in_range_pct"], "below_pct": prev_g["below_pct"],
                     "coverage_pct": prev_g["coverage_pct"]},
        "glucose_from": glucose_from,
        "log_from": log_from,
        "flags": week_flags(sessions, glucose, nights, days, low, glucose_from, log_from),
    }


def build_week(conn, ref_id: int) -> tuple[dict, str] | None:
    """The weekly summary's input: the week's numbers (ref_id = the Monday as YYYYMMDD), minus ids and
    epoch times the model has no use for, plus the flags to mention."""
    monday = date(ref_id // 10000, ref_id // 100 % 100, ref_id % 100)
    n = numbers(conn, monday)
    data = {
        "flags_must_mention": [f["text"] for f in n["flags"]],
        "week": n["label"], "complete": n["complete"],
        "by_sport": n["by_sport"], "zones_min": n["zones_min"], "glucose": n["glucose"],
        "sessions": [{k: v for k, v in s.items() if k not in ("id", "start", "flags")} | {"flags": [f["text"] for f in s["flags"]]}
                     for s in n["sessions"]],
        "nights_after_training": n["nights"],
        "days": [{k: v for k, v in d.items() if k not in ("date", "future")} for d in n["days"] if not d["future"]],
        "previous_week": n["previous"],
    }
    sha = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    return data, sha


def ref_of(monday: date) -> int:
    return monday.year * 10000 + monday.month * 100 + monday.day


@router.get("/weeks/{day}")
def get_week(day: date):
    derive.refresh()
    monday = monday_of(day)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        if monday > monday_of(datetime.now(LOCAL).date()):
            raise HTTPException(status_code=404, detail="that week has not started")
        return numbers(conn, monday)

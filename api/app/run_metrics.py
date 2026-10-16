"""Per-run numbers (roadmap step 7). Pure functions: no database, no clock, so each is unit-tested on its own.
app/derive.py loads the inputs, calls these and stores the results.

Glucose (window: start to 120 min after the end, as the spec's graph window minus the hour before)
  glucose_start / glucose_end  the reading nearest the start / end, within READING_NEAR_S; else None
  delta                        end - start
  rate_per_10min               delta per 10 min, over the time between those two readings (>= 5 min apart)
  nadir, nadir_minute          lowest reading from the start to 120 min after the end, minutes from the start
  min_below_low                minutes under the target's low end from the start to 120 min after the end;
                               a live reading covers 1 min, a 15-min history reading covers 15 min
  coverage_pct                 minutes of the activity itself that have a reading of their own (live readings
                               count one minute each, so a run without LibreLink open scores low)
  low_coverage                 coverage under 80 % (spec)

Context (from the log; None for activities that started before the log did, since unlogged is not zero)
  iob_at_start                 calculator.insulin_on_board: the calculator's own tested IOB, logged rapid doses
  min_since_last_bolus, last_bolus_units   last rapid dose in the 24 h before the start
  last_meal_carbs, min_since_last_meal     last carbs in the 24 h before the start
  pre_run_carbs                grams logged from 60 min before the start to the end (gels during count)
  h_since_basal                hours since the last basal dose, within 48 h
  local_time                   start time of day in Europe/Madrid

Training
  time_in_zone                 seconds per zone 1-5 from the 1 s heart rate (each record counts up to 5 s)
  hr_drift_pct                 aerobic decoupling: speed/HR in the second half against the first, over moving
                               records (>= 1 m/s); positive = HR drifted up for the same speed. Needs 20 min moving.
  grade_adjusted_pace_s        flat-equivalent pace (s/km) for runs, walks and hikes, from the energy cost of
                               running on a slope (Minetti et al. 2002, J Appl Physiol 93:1039-1046):
                               C(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6 J/kg/m,
                               i the gradient (clamped to +-0.45). Each segment of >= GRADE_SEGMENT_M counts as
                               distance x C(i)/C(0) on the flat. Altitude is smoothed over 15 s first.
"""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from .calculator import Dose, insulin_on_board

LOCAL = ZoneInfo("Europe/Madrid")
AFTER_S = 120 * 60
READING_NEAR_S = 10 * 60
LIVE_COVER_S = 60
HISTORY_COVER_S = 15 * 60
LOW_COVERAGE_PCT = 80
PRE_RUN_S = 60 * 60
LAST_EVENT_S = 24 * 3600
BASAL_S = 48 * 3600
MOVING_MPS = 1.0
DRIFT_MIN_S = 20 * 60
GRADE_SEGMENT_M = 20.0
GRADE_CLAMP = 0.45
ALT_SMOOTH_S = 15
RECORD_MAX_DT_S = 5
GAP_SPORTS = {"running", "walking", "hiking"}


def _round(v, places="0.1"):
    return None if v is None else Decimal(str(v)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


# --- glucose -------------------------------------------------------------------------------------


def _nearest(readings, t: datetime):
    best = None
    for r in readings:
        d = abs((r["ts"] - t).total_seconds())
        if d <= READING_NEAR_S and (best is None or d < abs((best["ts"] - t).total_seconds())):
            best = r
    return best


def glucose_metrics(readings: list[dict], start: datetime, end: datetime, low: int) -> dict:
    """readings: dicts with ts (aware), mg_dl, trend, live; any order. Window: start to end + 120 min."""
    readings = sorted(readings, key=lambda r: r["ts"])
    window_end = end + timedelta(seconds=AFTER_S)
    g0, g1 = _nearest(readings, start), _nearest(readings, end)
    out = {"glucose_start": g0["mg_dl"] if g0 else None, "trend_start": g0["trend"] if g0 else None,
           "glucose_end": g1["mg_dl"] if g1 else None, "delta": None, "rate_per_10min": None}
    if g0 and g1:
        out["delta"] = g1["mg_dl"] - g0["mg_dl"]
        span = (g1["ts"] - g0["ts"]).total_seconds()
        if span >= 300:
            out["rate_per_10min"] = _round(out["delta"] / span * 600)

    inside = [r for r in readings if start <= r["ts"] <= window_end]
    if inside:
        low_r = min(inside, key=lambda r: (r["mg_dl"], r["ts"]))
        out["nadir"] = low_r["mg_dl"]
        out["nadir_minute"] = round((low_r["ts"] - start).total_seconds() / 60)
    else:
        out["nadir"] = out["nadir_minute"] = None

    # Minutes under the low end: each reading covers its own resolution, never past the next reading.
    below = 0.0
    for i, r in enumerate(inside):
        cover = LIVE_COVER_S if r["live"] else HISTORY_COVER_S
        nxt = inside[i + 1]["ts"] if i + 1 < len(inside) else window_end
        seconds = max(0.0, min(cover, (nxt - r["ts"]).total_seconds(), (window_end - r["ts"]).total_seconds()))
        if r["mg_dl"] < low:
            below += seconds
    out["min_below_low"] = round(below / 60)

    minutes = max(1, round((end - start).total_seconds() / 60))
    during = {r["ts"].replace(second=0, microsecond=0) for r in readings if start <= r["ts"] <= end}
    out["coverage_pct"] = _round(min(100.0, 100.0 * len(during) / minutes))
    out["low_coverage"] = float(out["coverage_pct"]) < LOW_COVERAGE_PCT
    out["readings"] = len(inside)
    return out


# --- context -------------------------------------------------------------------------------------


def context(start: datetime, end: datetime, rapid: list[dict], basal: list[dict], carbs: list[dict],
            action_time_h: Decimal | None, log_started: datetime | None) -> dict:
    """rapid/basal: dicts with ts, units; carbs: dicts with ts, grams. Only entries not deleted."""
    local = start.astimezone(LOCAL)
    out = {"local_time": local.time().replace(second=0, microsecond=0), "log_covered": bool(log_started and log_started <= start)}
    keys = ("iob_at_start", "min_since_last_bolus", "last_bolus_units", "last_meal_carbs", "min_since_last_meal",
            "pre_run_carbs", "h_since_basal")
    if not out["log_covered"]:
        return {**out, **{k: None for k in keys}}

    def last_before(rows, within_s):
        rows = [r for r in rows if r["ts"] <= start and (start - r["ts"]).total_seconds() <= within_s]
        return max(rows, key=lambda r: r["ts"]) if rows else None

    bolus = last_before(rapid, LAST_EVENT_S)
    meal = last_before(carbs, LAST_EVENT_S)
    basal_d = last_before(basal, BASAL_S)
    minutes = lambda r: round((start - r["ts"]).total_seconds() / 60)  # noqa: E731
    out["iob_at_start"] = (
        _round(insulin_on_board([Dose(ts=d["ts"], units=Decimal(d["units"])) for d in rapid if d["ts"] <= start], start,
                                Decimal(action_time_h)), "0.01")
        if action_time_h is not None else None
    )
    out["min_since_last_bolus"] = minutes(bolus) if bolus else None
    out["last_bolus_units"] = _round(bolus["units"]) if bolus else None
    out["last_meal_carbs"] = _round(meal["grams"]) if meal else None
    out["min_since_last_meal"] = minutes(meal) if meal else None
    pre = [c for c in carbs if start - timedelta(seconds=PRE_RUN_S) <= c["ts"] <= end]
    out["pre_run_carbs"] = _round(sum(Decimal(c["grams"]) for c in pre)) if pre else Decimal(0)
    out["h_since_basal"] = _round((start - basal_d["ts"]).total_seconds() / 3600) if basal_d else None
    return out


# --- training ------------------------------------------------------------------------------------


def _dts(t: list[int]) -> list[float]:
    """Seconds each record stands for: the gap to the next one, capped (a pause is not time in a zone)."""
    return [min(RECORD_MAX_DT_S, t[i + 1] - t[i]) if i + 1 < len(t) else 1 for i in range(len(t))]


def time_in_zone(t: list[int], hr: list, floors: list[int]) -> list[int]:
    out = [0.0] * 5
    for dt, h in zip(_dts(t), hr):
        if h is not None:
            out[sum(1 for f in floors if h >= f)] += dt
    return [round(x) for x in out]


def hr_drift_pct(t: list[int], hr: list, speed: list):
    moving = [(dt, h, s) for dt, h, s in zip(_dts(t), hr, speed) if h and s is not None and s >= MOVING_MPS]
    total = sum(dt for dt, _, _ in moving)
    if total < DRIFT_MIN_S:
        return None
    half, acc, first, second = total / 2, 0.0, [], []
    for m in moving:
        (first if acc < half else second).append(m)
        acc += m[0]

    def ef(part):  # time-weighted speed per beat
        w = sum(dt for dt, _, _ in part)
        return (sum(dt * s for dt, _, s in part) / w) / (sum(dt * h for dt, h, _ in part) / w)

    e1, e2 = ef(first), ef(second)
    return _round((e1 - e2) / e1 * 100)


def minetti_cost(i: float) -> float:
    """Energy cost of running (J/kg/m) at gradient i, Minetti et al. 2002."""
    i = max(-GRADE_CLAMP, min(GRADE_CLAMP, i))
    return 155.4 * i**5 - 30.4 * i**4 - 43.3 * i**3 + 46.3 * i**2 + 19.5 * i + 3.6


def grade_adjusted_pace(sport: str, t: list[int], dist: list, alt: list, speed: list) -> dict:
    """{"avg_pace_s", "grade_adjusted_pace_s"} in s/km over moving segments; None where it does not apply."""
    none = {"avg_pace_s": None, "grade_adjusted_pace_s": None}
    if sport not in GAP_SPORTS or not any(a is not None for a in alt) or not any(d is not None for d in dist):
        return none
    # Altitude smoothed over ALT_SMOOTH_S (barometric and GPS altitude both jitter by a metre or more).
    smooth, lo, acc, n = [], 0, 0.0, 0
    for i in range(len(t)):
        if alt[i] is not None:
            acc, n = acc + alt[i], n + 1
        while t[i] - t[lo] >= ALT_SMOOTH_S:
            if alt[lo] is not None:
                acc, n = acc - alt[lo], n - 1
            lo += 1
        smooth.append(acc / n if n else None)

    moving_s = flat_m = real_m = 0.0
    seg_start = None
    for i in range(len(t)):
        if dist[i] is None or smooth[i] is None:
            continue
        if seg_start is None:
            seg_start = i
            continue
        d = dist[i] - dist[seg_start]
        if d < GRADE_SEGMENT_M:
            continue
        dt = t[i] - t[seg_start]
        if dt > 0 and d / dt >= MOVING_MPS:  # stops and walks to a halt do not count
            grade = (smooth[i] - smooth[seg_start]) / d
            moving_s += dt
            real_m += d
            flat_m += d * minetti_cost(grade) / minetti_cost(0.0)
        seg_start = i
    if real_m < 200 or moving_s <= 0:
        return none
    return {"avg_pace_s": _round(moving_s / real_m * 1000), "grade_adjusted_pace_s": _round(moving_s / flat_m * 1000)}


# --- flags ---------------------------------------------------------------------------------------
# Things a reader must not miss, decided by code, not by a model: shown on the run page, and given to the AI
# report, which must mention each one. Thresholds from the exercise consensus for type 1 diabetes
# (Riddell et al., Lancet Diabetes Endocrinol 2017;5:377-390): start aerobic exercise around 126-180 mg/dL;
# under 90 is a high risk of a low; above 270, check ketones when the high is unexplained, and do not exercise
# with blood ketones of 1.5 mmol/L or more; lows can come hours later, overnight.
# Kinds: "safety" (glucose), "log" (what the log can and cannot tell), "data" (how complete the readings are).
# Each flag also has a code, so the page can style one (went_low in the low colour) without reading its text.

KETONE_CHECK_MG_DL = 270
HIGH_MG_DL = 250
LOW_START_MG_DL = 90
LATE_LOW_FROM_HOUR = 16  # sessions ending from 16:00 local
LATE_LOW_MIN_MOVING = 45


def flags(*, glucose_start, points: list, duration_min: float, nadir, nadir_minute, min_below_low: int, low: int,
          coverage_pct, readings: int, log_covered: bool | None, min_since_last_bolus, end_local_hour: float,
          moving_min: float) -> list[dict]:
    """points: [(minutes from the start, mg/dL), ...] around the activity."""
    out = []

    def add(kind, code, text):
        out.append({"kind": kind, "code": code, "text": text})

    during = [mg for m, mg in points if 0 <= m <= duration_min]
    peak = max(during) if during else None
    top = max(v for v in (glucose_start, peak) if v is not None) if (glucose_start is not None or peak is not None) else None
    if top is not None and top > KETONE_CHECK_MG_DL:
        where = f"{glucose_start} at the start" if glucose_start is not None and glucose_start == top else f"up to {top} during it"
        add("safety", "ketones", f"Glucose was above {KETONE_CHECK_MG_DL} mg/dL ({where}). The exercise consensus advises checking "
                      "ketones before exercising at this level when the high is unexplained, and not exercising with "
                      "blood ketones of 1.5 mmol/L or more.")
    elif top is not None and top > HIGH_MG_DL:
        add("safety", "high", f"Glucose was above {HIGH_MG_DL} mg/dL (up to {top}): performance may drop, and hard efforts can "
                      "push it higher.")
    if glucose_start is not None and glucose_start < LOW_START_MG_DL:
        add("safety", "low_start", f"Started at {glucose_start} mg/dL, below {LOW_START_MG_DL}: the exercise consensus counts this as "
                      "a high risk of a low during aerobic exercise.")
    if nadir is not None and nadir < low:
        add("safety", "went_low", f"Went below {low} mg/dL: lowest {nadir} at {nadir_minute} min, {min_below_low} min below {low}.")
    if end_local_hour >= LATE_LOW_FROM_HOUR and moving_min >= LATE_LOW_MIN_MOVING:
        add("safety", "late_low", "An afternoon or evening session of 45 min or more: the risk of a delayed low, overnight, is higher.")
    if log_covered is False:
        add("log", "log_start", "Started before the log began: insulin and carbs before it are unknown.")
    elif log_covered and min_since_last_bolus is None:
        add("log", "no_bolus", "No rapid insulin logged in the 24 h before: insulin on board is 0 only if no dose went unlogged.")
    if readings and glucose_start is None:
        add("data", "no_start", "No reading within 10 min of the start, so there is no start value and no change.")
    if coverage_pct is not None and readings and coverage_pct < LOW_COVERAGE_PCT:
        add("data", "coverage", f"Coverage {coverage_pct:g} %: mostly 15-minute history. Live 1-minute readings arrive only while "
                    "LibreLink on the phone is connected to the sensor over Bluetooth.")
    return out

"""Per-run numbers (app/run_metrics.py): pure functions, each expected value worked out by hand."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app import run_metrics as rm

START = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)  # 09:00 in Madrid (CEST)
END = START + timedelta(minutes=40)


def at(minutes, mg, live=True, trend=3):
    return {"ts": START + timedelta(minutes=minutes), "mg_dl": mg, "trend": trend if live else None, "live": live}


# --- glucose -------------------------------------------------------------------------------------


def test_glucose_a_run_with_livelink_open():
    # Live every minute: 160 at the start, falling 2 per minute to 80 at 40 min, then flat at 80 for 20 min.
    readings = [at(m, 160 - 2 * m) for m in range(0, 41)] + [at(m, 80) for m in range(41, 61)]
    g = rm.glucose_metrics(readings, START, END, low=70)
    assert (g["glucose_start"], g["glucose_end"], g["delta"]) == (160, 80, -80)
    assert g["rate_per_10min"] == Decimal("-20.0")  # -80 over 40 min
    assert (g["nadir"], g["nadir_minute"]) == (80, 40)  # first time it reaches 80
    assert g["min_below_low"] == 0 and g["coverage_pct"] == Decimal("100.0") and g["low_coverage"] is False
    assert g["readings"] == 61 and g["trend_start"] == 3


def test_glucose_with_history_only_and_a_low_after():
    # 15-min history only: 150 at -5, 120 at 10, 100 at 25, 90 at 40, 65 at 55, 60 at 70, 75 at 85.
    pts = [(-5, 150), (10, 120), (25, 100), (40, 90), (55, 65), (70, 60), (85, 75)]
    g = rm.glucose_metrics([at(m, v, live=False) for m, v in pts], START, END, low=70)
    assert g["glucose_start"] == 150 and g["trend_start"] is None  # 5 min away: within 10 min
    assert g["glucose_end"] == 90 and g["rate_per_10min"] == Decimal("-13.3")  # -60 over 45 min
    assert (g["nadir"], g["nadir_minute"]) == (60, 70)
    assert g["min_below_low"] == 30  # 65 at 55 (15 min) and 60 at 70 (15 min)
    # 3 readings inside the 40 minutes (10, 25, 40): 3 / 40 = 7.5 %
    assert g["coverage_pct"] == Decimal("7.5") and g["low_coverage"] is True


def test_glucose_without_readings_near_the_start():
    g = rm.glucose_metrics([at(-30, 150, live=False), at(41, 100)], START, END, low=70)
    assert g["glucose_start"] is None and g["glucose_end"] == 100 and g["delta"] is None and g["rate_per_10min"] is None
    assert rm.glucose_metrics([], START, END, low=70)["nadir"] is None


# --- context -------------------------------------------------------------------------------------


def rapid(minutes_before, units):
    return {"ts": START - timedelta(minutes=minutes_before), "units": Decimal(units)}


def test_context():
    c = rm.context(
        START, END,
        rapid=[rapid(300, 6), rapid(150, 4), rapid(-10, 1)],  # the last one is during the run: not before
        basal=[rapid(14 * 60, 20)],
        carbs=[{"ts": START - timedelta(minutes=150), "grams": Decimal(60)},
               {"ts": START - timedelta(minutes=20), "grams": Decimal(15)},
               {"ts": START + timedelta(minutes=25), "grams": Decimal(25)}],
        action_time_h=Decimal(5), log_started=START - timedelta(days=3),
    )
    # IOB, linear over 5 h: 6 u at 300 min is spent; 4 u at 150 min is half left (2.00)
    assert c["iob_at_start"] == Decimal("2.00")
    assert (c["min_since_last_bolus"], c["last_bolus_units"]) == (150, Decimal("4.0"))
    assert (c["last_meal_carbs"], c["min_since_last_meal"]) == (Decimal("15.0"), 20)
    assert c["pre_run_carbs"] == Decimal("40.0")  # 15 at -20 and the 25 g gel during; the 60 g at -150 is too early
    assert c["h_since_basal"] == Decimal("14.0") and str(c["local_time"]) == "09:00:00" and c["log_covered"] is True


def test_context_before_the_log_started_is_unknown_not_zero():
    c = rm.context(START, END, [], [], [], Decimal(5), log_started=START + timedelta(days=2))
    assert c["log_covered"] is False and c["iob_at_start"] is None and c["pre_run_carbs"] is None
    c = rm.context(START, END, [], [], [], Decimal(5), log_started=None)
    assert c["log_covered"] is False and c["min_since_last_meal"] is None


def test_context_without_insulin_settings_has_no_iob():
    c = rm.context(START, END, [rapid(60, 3)], [], [], None, log_started=START - timedelta(days=1))
    assert c["iob_at_start"] is None and c["last_bolus_units"] == Decimal("3.0")


# --- training ------------------------------------------------------------------------------------


def test_time_in_zone_and_pauses():
    t = [0, 1, 2, 3, 60, 61]  # a 57 s pause after the 4th record counts as 5 s
    hr = [150, 165, 175, 185, 195, None]
    # zones from 162/171/181/191: 150 z1 (1 s), 165 z2 (1 s), 175 z3 (1 s), 185 z4 (5 s), 195 z5 (1 s), None skipped
    assert rm.time_in_zone(t, hr, [162, 171, 181, 191]) == [1, 1, 1, 5, 1]


def test_hr_drift():
    t = list(range(2400))  # 40 min at 3 m/s; HR 150 in the first half, 156 in the second
    hr = [150] * 1200 + [156] * 1200
    # ef1 = 3/150 = 0.02, ef2 = 3/156; (ef1 - ef2) / ef1 = 1 - 150/156 = 3.85 %
    assert rm.hr_drift_pct(t, hr, [3.0] * 2400) == Decimal("3.8")
    assert rm.hr_drift_pct(t[:600], hr[:600], [3.0] * 600) is None  # under 20 min moving


@pytest.mark.parametrize(
    "grade,expected_ratio",
    [(0.0, 1.0), (0.05, 3.6 / rm.minetti_cost(0.05)), (-0.05, 3.6 / rm.minetti_cost(-0.05))],
)
def test_grade_adjusted_pace_on_a_constant_slope(grade, expected_ratio):
    # 3 m/s for 20 min on a constant grade: pace 333.3 s/km; GAP = pace x C(0)/C(i)
    t = list(range(1200))
    dist = [3.0 * s for s in t]
    alt = [100 + grade * d for d in dist]
    g = rm.grade_adjusted_pace("running", t, dist, alt, [3.0] * 1200)
    assert g["avg_pace_s"] == Decimal("333.3")
    # Within 1.5 s/km: the 15 s altitude smoothing understates the slope over the first seconds.
    assert abs(float(g["grade_adjusted_pace_s"]) - 333.33 * expected_ratio) < 1.5


def test_grade_adjusted_pace_does_not_apply():
    t = list(range(600))
    assert rm.grade_adjusted_pace("cycling", t, [8.0 * s for s in t], [100.0] * 600, [8.0] * 600)["grade_adjusted_pace_s"] is None
    assert rm.grade_adjusted_pace("running", t, [3.0 * s for s in t], [None] * 600, [3.0] * 600)["avg_pace_s"] is None


# --- flags ---------------------------------------------------------------------------------------


def flag_args(**kw):
    base = dict(glucose_start=150, points=[(0, 150), (15, 140), (30, 130)], duration_min=30, nadir=120, nadir_minute=60,
                min_below_low=0, low=70, coverage_pct=Decimal("100.0"), readings=60, log_covered=True,
                min_since_last_bolus=120, end_local_hour=9.5, moving_min=30)
    return {**base, **kw}


def test_no_flags_for_an_ordinary_run():
    assert rm.flags(**flag_args()) == []


def test_flags_for_the_high_run():
    # The 13 Oct run: 341 at the start, up to 371, 5.3 % coverage, nothing rapid logged in the 24 h before.
    f = rm.flags(**flag_args(glucose_start=341, points=[(-5, 338), (10, 355), (26, 371), (38, 343)], duration_min=38.4,
                             nadir=200, nadir_minute=123, coverage_pct=Decimal("5.3"), readings=18,
                             min_since_last_bolus=None, end_local_hour=18.6, moving_min=38.4))
    assert [x["kind"] for x in f] == ["safety", "log", "data"]
    assert [x["code"] for x in f] == ["ketones", "no_bolus", "coverage"]
    assert "above 270 mg/dL (up to 371 during it)" in f[0]["text"] and "ketones" in f[0]["text"]
    assert "0 only if no dose went unlogged" in f[1]["text"] and "Bluetooth" in f[2]["text"] and "5.3 %" in f[2]["text"]


def test_flags_low_start_low_during_and_late_session():
    f = rm.flags(**flag_args(glucose_start=85, nadir=62, nadir_minute=40, min_below_low=12, end_local_hour=19.0, moving_min=60))
    texts = [x["text"] for x in f]
    assert texts[0].startswith("Started at 85 mg/dL, below 90")
    assert texts[1] == "Went below 70 mg/dL: lowest 62 at 40 min, 12 min below 70."
    assert "delayed low" in texts[2]
    assert [x["code"] for x in f] == ["low_start", "went_low", "late_low"]


def test_flags_between_250_and_270_and_unknown_log():
    f = rm.flags(**flag_args(glucose_start=240, points=[(10, 262)], log_covered=False))
    assert f[0]["text"].startswith("Glucose was above 250 mg/dL (up to 262)") and f[1]["kind"] == "log"
    assert "before the log began" in f[1]["text"]

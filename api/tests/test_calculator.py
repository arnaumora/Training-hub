"""Calculator unit tests. Pure function: no database needed, always run.

Every case states its hand calculation in the comment. Test settings only (shaped like a saved
version); times are Europe/Madrid unless written in UTC.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.calculator import Dose, Glucose, calculate, insulin_on_board, slot_at

MAD = ZoneInfo("Europe/Madrid")
UTC = ZoneInfo("UTC")

SETTINGS = {
    "version": 3,
    "meal_slots": [
        {"name": "Breakfast", "start": "07:00", "ratio": 2.5},
        {"name": "Morning snack", "start": "10:30", "ratio": 2.2},
        {"name": "Lunch", "start": "13:30", "ratio": 1.6},
        {"name": "Afternoon snack", "start": "17:00", "ratio": 1.5},
        {"name": "Dinner", "start": "20:30", "ratio": 1.0},
    ],
    "isf": Decimal("40"),
    "correct_above": 160,
    "correct_to": 160,
    "action_time_h": Decimal("5"),
    "action_time_provisional": True,
    "pen_increment": Decimal("1"),
    "racion_g": Decimal("10"),
}


def at(h, m=0, day=12, month=10):
    return datetime(2026, month, day, h, m, tzinfo=MAD)


def g(now, mg, minutes_old=2, trend=3):
    return Glucose(ts=now - timedelta(minutes=minutes_old), mg_dl=mg, trend=trend)


def calc(now, carbs_g=None, glucose=None, doses=(), first=None, settings=SETTINGS):
    return calculate(settings, now, None if carbs_g is None else Decimal(carbs_g), glucose, list(doses), first)


# --- carbs part ---------------------------------------------------------------------------


def test_01_lunch_whole_raciones():
    # 13:45 is Lunch (1.6). 50 g = 5 rac. 5 * 1.6 = 8.0. Glucose 120: no correction. Suggest 8.
    now = at(13, 45)
    r = calc(now, 50, g(now, 120))
    assert (r.slot, r.raciones, r.carbs_units, r.correction_units, r.suggested_units) == ("Lunch", 5, 8, 0, 8)


def test_02_rounds_down_to_whole_units():
    # 45 g = 4.5 rac. 4.5 * 1.6 = 7.2. Floor to 1 u: 7.
    now = at(14, 0)
    r = calc(now, 45, g(now, 120))
    assert r.total_units == Decimal("7.2") and r.suggested_units == 7


def test_03_breakfast_with_correction():
    # 08:00 Breakfast (2.5). 30 g = 3 rac -> 7.5. Glucose 220: (220 - 160) / 40 = 1.5. Total 9.0 -> 9.
    now = at(8, 0)
    r = calc(now, 30, g(now, 220))
    assert (r.carbs_units, r.correction_units, r.total_units, r.suggested_units) == (Decimal("7.5"), Decimal("1.5"), 9, 9)


def test_04_iob_cancels_the_correction_only():
    # As 03, plus 4 u at 06:30 (1.5 h ago): IOB = 4 * (1 - 1.5/5) = 2.8.
    # Correction 1.5 - min(2.8, 1.5) = 0. Carbs part untouched: 7.5 -> 7.
    now = at(8, 0)
    r = calc(now, 30, g(now, 220), [Dose(at(6, 30), Decimal(4))])
    assert r.iob_units == Decimal("2.8") and r.iob_used == Decimal("1.5")
    assert r.total_units == Decimal("7.5") and r.suggested_units == 7


def test_05_iob_smaller_than_correction():
    # No carbs. Glucose 260: (260 - 160) / 40 = 2.5. 2 u 2.5 h ago: IOB = 2 * 0.5 = 1.0.
    # Net correction 2.5 - 1.0 = 1.5 -> 1.
    now = at(16, 0)
    r = calc(now, None, g(now, 260), [Dose(now - timedelta(hours=2, minutes=30), Decimal(2))])
    assert (r.correction_units, r.iob_units, r.total_units, r.suggested_units) == (Decimal("2.5"), 1, Decimal("1.5"), 1)


def test_06_iob_never_reduces_the_carbs_part():
    # 21:00 Dinner (1.0). 60 g = 6 rac -> 6.0. Glucose 150: no correction.
    # 8 u 1 h ago: IOB 8 * 0.8 = 6.4, but there is no correction to offset: still 6.
    now = at(21, 0)
    r = calc(now, 60, g(now, 150), [Dose(now - timedelta(hours=1), Decimal(8))])
    assert r.iob_units == Decimal("6.4") and r.iob_used == 0 and r.suggested_units == 6


def test_07_dose_older_than_action_time_counts_zero():
    # 5 u exactly 5 h ago and 3 u 6 h ago: both past the 5 h action time. IOB 0.
    now = at(18, 0)
    assert insulin_on_board([Dose(now - timedelta(hours=5), Decimal(5)), Dose(now - timedelta(hours=6), Decimal(3))], now, Decimal(5)) == 0


def test_08_iob_sums_several_doses():
    # 3 u 1 h ago -> 3 * 0.8 = 2.4; 2 u 4 h ago -> 2 * 0.2 = 0.4. IOB 2.8.
    now = at(18, 0)
    doses = [Dose(now - timedelta(hours=1), Decimal(3)), Dose(now - timedelta(hours=4), Decimal(2))]
    assert insulin_on_board(doses, now, Decimal(5)) == Decimal("2.8")


def test_09_dose_logged_a_moment_ahead_counts_fully():
    # A dose 2 min "in the future" (clock skew): elapsed counted as 0, full 4 u.
    now = at(12, 0)
    assert insulin_on_board([Dose(now + timedelta(minutes=2), Decimal(4))], now, Decimal(5)) == 4


def test_10_correction_only_strictly_above_threshold():
    # 160 is not above 160: correction 0. 161: (161 - 160) / 40 = 0.025.
    now = at(12, 0)
    assert calc(now, None, g(now, 160)).correction_units == 0
    assert calc(now, None, g(now, 161)).correction_units == Decimal("0.025")


def test_11_correct_above_and_correct_to_differ():
    # Correct above 180, down to 120. Glucose 200: (200 - 120) / 40 = 2.0 -> 2. Glucose 170: none.
    s = {**SETTINGS, "correct_above": 180, "correct_to": 120}
    now = at(12, 0)
    assert calc(now, None, g(now, 200), settings=s).suggested_units == 2
    assert calc(now, None, g(now, 170), settings=s).correction_units == 0


def test_12_low_glucose_suggests_nothing():
    # Glucose 65 < 70: no dose, even with 40 g of carbs.
    now = at(13, 45)
    r = calc(now, 40, g(now, 65))
    assert r.suggested_units is None and any("below 70" in w for w in r.warnings)


def test_13_stale_reading_warns_but_still_corrects():
    # Reading 15 min old: warning; correction still (220 - 160) / 40 = 1.5.
    now = at(12, 0)
    r = calc(now, None, g(now, 220, minutes_old=15))
    assert r.correction_units == Decimal("1.5") and any("15 min old" in w for w in r.warnings)


def test_14_reading_older_than_30_min_leaves_correction_out():
    # 31 min old: no correction computed; carbs only. 13:45, 30 g = 3 rac * 1.6 = 4.8 -> 4.
    now = at(13, 45)
    r = calc(now, 30, g(now, 250, minutes_old=31))
    assert r.correction_units is None and r.suggested_units == 4
    assert any("last 30 min" in w for w in r.warnings)


def test_15_no_reading_at_all():
    now = at(13, 45)
    r = calc(now, 30, None)
    assert r.correction_units is None and r.glucose_age_min is None and r.suggested_units == 4


def test_16_falling_fast_warns_without_changing_numbers():
    # Trend 1 (falling fast): same 8 u as test 01, plus a warning.
    now = at(13, 45)
    r = calc(now, 50, g(now, 120, trend=1))
    assert r.suggested_units == 8 and any("falling fast" in w for w in r.warnings)


def test_17_slot_wraps_past_midnight():
    # 02:00 is before Breakfast (07:00): still Dinner (1.0). 30 g = 3 rac -> 3.
    now = at(2, 0)
    r = calc(now, 30, g(now, 120))
    assert r.slot == "Dinner" and r.suggested_units == 3


def test_18_slot_boundaries():
    # 10:29 Breakfast, 10:30 Morning snack.
    assert slot_at(SETTINGS["meal_slots"], at(10, 29))["name"] == "Breakfast"
    assert slot_at(SETTINGS["meal_slots"], at(10, 30))["name"] == "Morning snack"


def test_19_slot_uses_madrid_time_in_summer_and_winter():
    # 11:45 UTC on 12 Oct (CEST, UTC+2) = 13:45 Lunch. 12:45 UTC on 10 Nov (CET, UTC+1) = 13:45 Lunch.
    # 12:15 UTC on 10 Nov = 13:15 CET: still Morning snack.
    slots = SETTINGS["meal_slots"]
    assert slot_at(slots, datetime(2026, 10, 12, 11, 45, tzinfo=UTC))["name"] == "Lunch"
    assert slot_at(slots, datetime(2026, 11, 10, 12, 45, tzinfo=UTC))["name"] == "Lunch"
    assert slot_at(slots, datetime(2026, 11, 10, 12, 15, tzinfo=UTC))["name"] == "Morning snack"


def test_20_half_unit_pen():
    # Pen increment 0.5. 45 g at lunch: 7.2 -> 7.0. 47.5 g: 4.75 * 1.6 = 7.6 -> 7.5.
    s = {**SETTINGS, "pen_increment": Decimal("0.5")}
    now = at(14, 0)
    assert calc(now, 45, g(now, 120), settings=s).suggested_units == 7
    assert calc(now, Decimal("47.5"), g(now, 120), settings=s).suggested_units == Decimal("7.5")


def test_21_racion_size_from_settings():
    # Racion 12 g: 36 g = 3 rac. Lunch 3 * 1.6 = 4.8 -> 4.
    s = {**SETTINGS, "racion_g": Decimal("12")}
    now = at(14, 0)
    r = calc(now, 36, g(now, 120), settings=s)
    assert r.raciones == 3 and r.suggested_units == 4


def test_22_nothing_to_cover_suggests_zero():
    now = at(12, 0)
    assert calc(now, None, g(now, 120)).suggested_units == 0


def test_23_young_log_warns_only_on_a_correction():
    # Usable from the first day (2026-10-16); under 14 days of logged rapid doses, a correction is flagged.
    now = at(12, 0)
    young = "logged for under 14 days"
    first = now - timedelta(days=14)
    assert not any(young in w for w in calc(now, 30, g(now, 220), first=first).warnings)
    for f in (first + timedelta(hours=1), None):
        r = calc(now, 30, g(now, 220), first=f)
        assert any(young in w for w in r.warnings) and r.suggested_units is not None
        assert not any(young in w for w in calc(now, 30, g(now, 120), first=f).warnings)  # no correction


def test_24_provisional_action_time_and_exercise_are_standing_notes_not_warnings():
    now = at(12, 0)
    r = calc(now, 30, g(now, 120))
    assert any("provisional" in x for x in r.notes) and any("Exercise" in x for x in r.notes)
    assert r.warnings == []  # a fresh, stable, in-range reading: nothing urgent to say
    r = calc(now, 30, g(now, 120), settings={**SETTINGS, "action_time_provisional": False})
    assert not any("provisional" in x for x in r.notes)


def test_25_dict_shape_for_the_ui():
    # Breakfast 30 g at 220 with 2.8 u active (test 04), as JSON numbers.
    now = at(8, 0)
    d = calc(now, 30, g(now, 220), [Dose(at(6, 30), Decimal(4))]).as_dict()
    assert d["carbs_units"] == 7.5 and d["correction_units"] == 1.5 and d["iob_units"] == 2.8
    assert d["iob_used"] == 1.5 and d["total_units"] == 7.5 and d["suggested_units"] == 7
    assert d["glucose"] == {"mg_dl": 220, "trend": 3, "age_min": 2} and d["settings_version"] == 3
    assert (d["isf"], d["correct_above"], d["correct_to"], d["ratio"], d["slot"]) == (40, 160, 160, 2.5, "Breakfast")


@pytest.mark.parametrize("carbs,glucose,expected", [(0, 120, 0), (10, 120, 1), (20, 175, 3)])
def test_26_small_amounts(carbs, glucose, expected):
    # Lunch 1.6: 10 g -> 1.6 -> 1. 20 g at 175: 3.2 + (175 - 160) / 40 = 3.2 + 0.375 = 3.575 -> 3.
    now = at(14, 0)
    assert calc(now, carbs, g(now, glucose)).suggested_units == expected

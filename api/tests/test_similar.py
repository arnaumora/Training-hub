"""Similar runs (app/similar.py): a pure function, each expected value worked out by hand."""

from app.similar import MEAL_CAP, rank


def run(i, g, iob=None, meal=None, trend=None, delta=-20, nadir=100, below=0, covered=True):
    return {"id": i, "start": 1000 + i, "glucose_start": g, "iob_at_start": iob, "min_since_last_meal": meal,
            "trend_start": trend, "delta": delta, "nadir": nadir, "min_below_low": below, "log_covered": covered}


def ids(out):
    return [r["id"] for r in out["runs"]]


def test_glucose_only():
    runs = [run(1, 100), run(2, 160), run(3, 140), run(4, None)]
    out = rank(runs, {"glucose_start": 150}, k=2)
    # 160 and 140 are both 10 away (1/3 unit); the tie goes to the more recent one (3)
    assert ids(out) == [3, 2] and out["runs"][0]["distance"] == 0.33
    assert out["used"] == ["glucose_start"] and out["candidates"] == 3 and out["skipped"] == 0


def test_units_weigh_each_part():
    # Want 150 mg/dL with 2 u. Run 1: same glucose, 1 u off (distance 1). Run 2: 15 mg/dL off, same IOB (0.5).
    out = rank([run(1, 150, iob=3), run(2, 165, iob=2)], {"glucose_start": 150, "iob_at_start": 2})
    assert ids(out) == [2, 1] and [r["distance"] for r in out["runs"]] == [0.5, 1.0]


def test_a_part_the_run_does_not_have_skips_it():
    # Run 2 started before the log: its IOB is unknown, so it is skipped rather than treated as 0.
    out = rank([run(1, 150, iob=1), run(2, 150, iob=None, covered=False)], {"glucose_start": 150, "iob_at_start": 0})
    assert ids(out) == [1] and out["skipped"] == 1


def test_meal_time_is_capped_and_no_meal_counts_as_long_ago():
    runs = [
        run(1, 150, meal=None, covered=True),   # no carbs in the 24 h before: MEAL_CAP
        run(2, 150, meal=600),                  # capped to MEAL_CAP
        run(3, 150, meal=60),
        run(4, 150, meal=None, covered=False),  # before the log: unknown, skipped
    ]
    out = rank(runs, {"min_since_last_meal": 480})  # want: capped to 300
    assert ids(out)[:2] == [2, 1] and out["runs"][0]["distance"] == 0 and out["skipped"] == 1
    assert out["runs"][2]["id"] == 3 and out["runs"][2]["distance"] == (MEAL_CAP - 60) / 60


def test_summary():
    runs = [run(1, 150, delta=-40, nadir=62, below=8), run(2, 150, delta=-10, nadir=120), run(3, 150, delta=-25, nadir=95)]
    s = rank(runs, {"glucose_start": 150})["summary"]
    assert s == {"n": 3, "median_delta": -25, "lowest": 62, "went_low": 1}
    assert rank([], {"glucose_start": 150})["summary"] == {"n": 0, "median_delta": None, "lowest": None, "went_low": 0}

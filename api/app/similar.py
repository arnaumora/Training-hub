"""Similar runs (roadmap step 7, change 4): given a starting state, the past activities that started closest
to it, and what happened in them. A pure function, so it is unit-tested on its own.

The state has up to four parts; the ones left out do not count:
  glucose_start         mg/dL      one SCALES unit = 30 mg/dL
  trend_start           1-5        one unit = one arrow step (falling fast .. rising fast)
  iob_at_start          u          one unit = 1 u
  min_since_last_meal   min        one unit = 60 min; both sides held to MEAL_CAP (after 5 h a meal no longer
                                   matters), and a run with no carbs in the 24 h before counts as MEAL_CAP

Distance: the square root of the sum of squared differences in those units (so 30 mg/dL apart counts as
much as 1 u of IOB apart, or an hour between meals). A run is a candidate only if it has a glucose at its
start (so there is something to report) and a value for every part of the state that was given; the others
are counted as `skipped`, never guessed. Ties go to the more recent run.

What happened: change, rate per 10 min, lowest (and when), minutes below the low, pre-run carbs; and over the
k closest, the median change, the lowest reading and how many went below the low.
"""

import math
from statistics import median

SCALES = {"glucose_start": 30.0, "trend_start": 1.0, "iob_at_start": 1.0, "min_since_last_meal": 60.0}
MEAL_CAP = 300


def _value(run: dict, key: str):
    v = run.get(key)
    if key == "min_since_last_meal":
        if v is None and run.get("log_covered"):
            return MEAL_CAP  # the log covers the run and shows no carbs in the 24 h before
        return None if v is None else min(v, MEAL_CAP)
    return v


def rank(runs: list[dict], state: dict, k: int = 5) -> dict:
    used = [key for key in SCALES if state.get(key) is not None]
    want = {key: (min(state[key], MEAL_CAP) if key == "min_since_last_meal" else state[key]) for key in used}
    with_glucose = [r for r in runs if r.get("glucose_start") is not None]
    candidates, skipped = [], 0
    for r in with_glucose:
        values = {key: _value(r, key) for key in used}
        if any(v is None for v in values.values()):
            skipped += 1
            continue
        d = math.sqrt(sum(((values[key] - want[key]) / SCALES[key]) ** 2 for key in used))
        candidates.append((d, -r["start"], r))
    candidates.sort(key=lambda c: (c[0], c[1]))
    closest = [{**r, "distance": round(d, 2)} for d, _, r in candidates[:k]]
    deltas = [r["delta"] for r in closest if r.get("delta") is not None]
    nadirs = [r["nadir"] for r in closest if r.get("nadir") is not None]
    return {
        "used": used,
        "runs": closest,
        "candidates": len(candidates),
        "skipped": skipped,
        "summary": {
            "n": len(closest),
            "median_delta": median(deltas) if deltas else None,
            "lowest": min(nadirs) if nadirs else None,
            "went_low": sum(1 for r in closest if (r.get("min_below_low") or 0) > 0),
        },
    }

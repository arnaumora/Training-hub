"""Planned vs done (app/plan_calendar.py): matching, statuses, and the endpoints against a real Postgres 16."""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import TEST_PASSWORD

from app.plan_calendar import build, day_status, match, pace_zone_of, plan_sport, session_status, target

L = ZoneInfo("Europe/Madrid")
TODAY = date(2026, 10, 14)


def S(i, day, sport="running", planned_m=None, hour=8):
    return {"id": i, "calendar": "c", "day": day, "sport": sport, "title": f"s{i}", "description": None, "planned_m": planned_m,
            "start_ts": None if sport == "rest" else datetime(day.year, day.month, day.day, hour, tzinfo=L)}


def A(i, day, sport="running", distance_m=5000.0, hour=9):
    return {"id": i, "day": day, "sport": sport, "fit_sport": sport, "distance_m": distance_m,
            "start": datetime(day.year, day.month, day.day, hour, tzinfo=L).timestamp()}


D = date(2026, 10, 12)  # Monday


def test_plan_sport():
    assert [plan_sport(s) for s in ("running", "swimming", "cycling", "training", "fitness_equipment", "walking")] == [
        "running", "swimming", "cycling", "strength", "strength", "other"]


@pytest.mark.parametrize(
    "title, zone",
    [
        ("Easy 6 km + strides", "easy"), ("Shakeout 3 km", "easy"), ("Long run 12 km", "long"),
        ("Long 14 km, progressive", "long"), ("Steady 2x8min", "steady"), ("Tempo 20 min", "tempo"),
        ("Threshold 4x4min", "threshold"), ("Race pace 3x10min", "race"), ("HALF MARATHON 21.1 km", "race"),
        ("Hill sprints 8x12s", None), ("Reps 6x800m", None), ("VO2 5x3min", None), ("5K TIME TRIAL", None),
        ("10K RACE / time trial", None), ("Primer 6x1min", None),
    ],
)
def test_pace_zone_of(title, zone):
    assert pace_zone_of(title) == zone


def test_target():
    zones = {"easy": [355, 385], "threshold": [290, 300]}
    run = {"sport": "running", "title": "Easy 5 km"}
    assert target(run, zones) == {"zone": "easy", "name": "Easy", "from_s": 355, "to_s": 385}
    assert target({**run, "title": "Tempo 20 min"}, zones) is None  # no tempo zone saved
    assert target({**run, "sport": "swimming"}, zones) is None and target(run, {}) is None


def test_same_day_same_sport_longest_wins():
    sessions = [S(1, D), S(2, D, "swimming")]
    acts = [A(10, D, distance_m=3000), A(11, D, distance_m=6000, hour=18), A(12, D, "swimming", 1800), A(13, date(2026, 10, 13))]
    assert match(sessions, acts, {}) == {1: (11, "auto"), 2: (12, "auto")}


def test_two_sessions_two_activities_in_order():
    sessions = [S(1, D, hour=7), S(2, D, hour=18)]
    acts = [A(10, D, distance_m=4000), A(11, D, distance_m=8000)]
    assert match(sessions, acts, {}) == {1: (11, "auto"), 2: (10, "auto")}


def test_rest_takes_nothing_and_links_win():
    sessions = [S(1, D, "rest"), S(2, date(2026, 10, 13)), S(3, date(2026, 10, 13))]
    acts = [A(10, D), A(11, date(2026, 10, 13))]
    # Session 2 was done on Monday (moved by hand); session 3 is marked not done, so 11 stays free.
    links = {2: {"kind": "activity", "activity_id": 10}, 3: {"kind": "none", "activity_id": None}}
    assert match(sessions, acts, links) == {2: (10, "manual")}


@pytest.mark.parametrize(
    "planned_m, distance, link, day, status",
    [
        (6000, 6000, None, D, "done"),
        (6000, 4800, None, D, "done"),  # exactly 80 %
        (6000, 4790, None, D, "partial"),
        (None, 1000, None, D, "done"),  # no distance in the plan: any matching activity
        (6000, None, None, D, "missed"),  # no activity, day over
        (6000, None, None, TODAY, "planned"),  # today: still open
        (6000, None, None, date(2026, 10, 20), "planned"),
        (None, None, {"kind": "done"}, D, "done"),  # gym marked done
    ],
)
def test_session_status(planned_m, distance, link, day, status):
    a = None if distance is None else A(10, day, distance_m=distance)
    assert session_status(S(1, day, planned_m=planned_m), a, link, TODAY) == status


@pytest.mark.parametrize(
    "statuses, unplanned, day, status",
    [
        ([], 0, D, "rest"),
        (["rest"], 0, D, "rest"),
        (["rest"], 1, D, "unplanned"),
        ([], 2, D, "unplanned"),
        (["done", "done"], 0, D, "done"),
        (["missed"], 0, D, "missed"),
        (["done", "missed"], 0, D, "partial"),
        (["partial"], 0, D, "partial"),
        (["planned"], 0, TODAY, "planned"),
        (["done", "planned"], 0, TODAY, "planned"),
        (["done"], 0, TODAY, "done"),
    ],
)
def test_day_status(statuses, unplanned, day, status):
    assert day_status(statuses, unplanned, day, TODAY) == status


def test_swapped_week_is_matched_as_run():
    # Planned: easy 5 on Saturday, long 12 on Sunday. Run: the long one on Saturday, the easy one on Sunday.
    sat, sun = date(2026, 10, 17), date(2026, 10, 18)
    sessions = [S(1, sat, planned_m=5000), S(2, sun, planned_m=12000)]
    acts = [A(10, sat, distance_m=12100), A(11, sun, distance_m=5000)]
    assert match(sessions, acts, {}) == {1: (11, "week"), 2: (10, "week")}


def test_similar_sessions_stay_on_their_days():
    # Easy 5 and easy 6, run as 6.1 and 5.0 on their days: both done where they are, nothing moves.
    tue, thu = date(2026, 10, 13), date(2026, 10, 15)
    sessions = [S(1, tue, planned_m=5000), S(2, thu, planned_m=6000)]
    acts = [A(10, tue, distance_m=6100), A(11, thu, distance_m=5000)]
    assert match(sessions, acts, {}) == {1: (10, "auto"), 2: (11, "auto")}


def test_moved_session_fills_a_missed_one():
    # Thursday's easy run done on Wednesday: matched, Thursday not missed.
    wed, thu = date(2026, 10, 14), date(2026, 10, 15)
    assert match([S(1, thu, planned_m=6000)], [A(10, wed, distance_m=6050)], {}) == {1: (10, "week")}


def test_no_move_when_the_distance_does_not_fit_or_across_weeks():
    wed, thu, next_mon = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 19)
    # A 2 km jog on Wednesday is not Thursday's 10 km.
    assert match([S(1, thu, planned_m=10000)], [A(10, wed, distance_m=2000)], {}) == {}
    # On the same day it still counts (partial), as before.
    assert match([S(1, thu, planned_m=10000)], [A(10, thu, distance_m=2000)], {}) == {1: (10, "auto")}
    # Sunday's session is not done by next Monday's run: weeks do not mix.
    assert match([S(1, date(2026, 10, 18), planned_m=6000)], [A(10, next_mon, distance_m=6000)], {}) == {}
    assert wed < thu


def test_build_a_week():
    tue, wed, sat, sun = (date(2026, 10, d) for d in (13, 14, 17, 18))
    sessions = [S(1, D, "strength"), S(2, tue, planned_m=6000), S(3, sat, "rest"), S(4, sun, planned_m=7000)]
    acts = [A(10, tue, distance_m=6010), A(11, sat, distance_m=7720)]
    out = build(D, sun, date(2026, 10, 19), sessions, acts, {1: {"kind": "done", "activity_id": None}})
    days = {d["day"]: d for d in out["days"]}
    assert days["2026-10-12"]["status"] == "done" and days["2026-10-12"]["sessions"][0]["link"] == "done"
    assert (days["2026-10-13"]["status"], days["2026-10-13"]["sessions"][0]["activity_id"]) == ("done", 10)
    # Long run done on Saturday, a rest day: matched within the week to Sunday's long run.
    sun_s = days["2026-10-18"]["sessions"][0]
    assert (days["2026-10-18"]["status"], sun_s["link"], sun_s["activity_day"]) == ("done", "week", "2026-10-17")
    assert (days["2026-10-17"]["status"], days["2026-10-17"]["unplanned"]) == ("rest", [])
    assert days["2026-10-14"]["status"] == "rest" and len(out["days"]) == 7
    assert set(out["activities"]) == {"10", "11"}

    # Linked by hand: Sunday's session done by Saturday's run.
    out = build(D, sun, date(2026, 10, 19), sessions, acts, {4: {"kind": "activity", "activity_id": 11}})
    days = {d["day"]: d for d in out["days"]}
    sun_s = days["2026-10-18"]["sessions"][0]
    assert (days["2026-10-18"]["status"], sun_s["link"], sun_s["activity_day"]) == ("done", "activity", "2026-10-17")
    assert days["2026-10-17"]["status"] == "rest" and days["2026-10-17"]["unplanned"] == []
    assert days["2026-10-12"]["status"] == "missed"  # gym not marked this time


# Endpoints against Postgres.

db = pytest.mark.skipif(not os.environ.get("TEST_PG_HOST"), reason="TEST_PG_HOST not set")


def _ics(day, *events):
    body = "".join(
        f"BEGIN:VEVENT\nUID:{uid}\nDTSTART;TZID=Europe/Madrid:{day.strftime('%Y%m%d')}T{hh}0000\nSUMMARY:{title}\nCATEGORIES:{cat}\nEND:VEVENT\n"
        for uid, hh, title, cat in events
    )
    return f"BEGIN:VCALENDAR\nX-WR-CALNAME:Plan\n{body}END:VCALENDAR\n".encode()


@db
def test_endpoints(env, tmp_path, monkeypatch):
    from test_activities import RUN

    c, _ = env
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    assert c.post("/api/login", json={"password": TEST_PASSWORD}).status_code == 204
    act = c.post("/api/activities/fit", content=RUN).json()["activities"][0]
    day = datetime.fromtimestamp(act["start"], L).date()
    km = act["distance_m"] / 1000
    # A run planned 10 % longer than done (done), a gym session, and a second run planned twice as long (partial
    # if it took the activity, but the first takes it).
    c.post("/api/plan/ics", content=_ics(day, ("r1", "07", f"Easy {km * 1.1:.1f} km", "RUN"), ("g1", "18", "Gym A", "GYM")))
    iso = day.isoformat()
    cal = c.get("/api/calendar", params={"start": iso, "end": iso}).json()
    d = cal["days"][0]
    run, gym = sorted(d["sessions"], key=lambda s: s["sport"])
    assert (run["status"], run["activity_id"], run["link"]) == ("done", act["id"], "auto")
    assert run["target"] == {"zone": "easy", "name": "Easy", "from_s": 355, "to_s": 385} and gym["target"] is None
    assert gym["status"] in ("missed", "planned") and gym["activity_id"] is None
    assert cal["activities"][str(act["id"])]["sport"] == act["sport"]

    # Gym marked done; the run marked not done (the activity becomes unplanned); then back to automatic.
    assert c.put(f"/api/calendar/sessions/{gym['id']}/link", json={"kind": "done"}).status_code == 204
    assert c.put(f"/api/calendar/sessions/{run['id']}/link", json={"kind": "none"}).status_code == 204
    d = c.get("/api/calendar", params={"start": iso, "end": iso}).json()["days"][0]
    st = {s["sport"]: (s["status"], s["link"]) for s in d["sessions"]}
    assert st["strength"] == ("done", "done") and st["running"][1] == "none" and d["unplanned"] == [act["id"]]
    assert c.delete(f"/api/calendar/sessions/{run['id']}/link").status_code == 204

    # Candidates and a link by hand to the gym session (any sport may be linked).
    cands = c.get(f"/api/calendar/sessions/{gym['id']}/candidates").json()
    assert [x["id"] for x in cands] == [act["id"]]
    assert c.put(f"/api/calendar/sessions/{gym['id']}/link", json={"kind": "activity", "activity_id": act["id"]}).status_code == 204
    d = c.get("/api/calendar", params={"start": iso, "end": iso}).json()["days"][0]
    st = {s["sport"]: (s["status"], s["activity_id"]) for s in d["sessions"]}
    assert st["strength"] == ("done", act["id"]) and st["running"][1] is None  # the run lost it to the link

    # Refusals.
    assert c.put(f"/api/calendar/sessions/{gym['id']}/link", json={"kind": "activity"}).status_code == 422
    assert c.put(f"/api/calendar/sessions/{gym['id']}/link", json={"kind": "activity", "activity_id": 999999}).status_code == 404
    assert c.put("/api/calendar/sessions/999999/link", json={"kind": "done"}).status_code == 404
    assert c.get("/api/calendar", params={"start": "2026-10-01", "end": "2026-12-31"}).status_code == 422
    assert c.get("/api/calendar", params={"start": "2026-10-02", "end": "2026-10-01"}).status_code == 422

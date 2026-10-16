"""Planned vs done (roadmap step 9, change 2): the plan's sessions next to the activities, day by day.

GET    /api/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD   days from start to end (inclusive, at most 62)
GET    /api/calendar/sessions/{id}/candidates           activities that could have done a session: 3 days
                                                        either side, any sport, not linked to another session
PUT    /api/calendar/sessions/{id}/link                 {"kind": "activity", "activity_id": n} | {"kind": "done"}
                                                        | {"kind": "none"}: see migration 0020
DELETE /api/calendar/sessions/{id}/link                 back to automatic matching

Matching (agreed 2026-10-13, within the week since 2026-10-14), computed on every read so it follows new activities and re-imported plans:
- A link set by hand wins. Otherwise, per week (Monday to Sunday, local days) and sport, sessions and
  activities are paired at the lowest cost: distance fit plus a cost per day moved (see match()). So sessions
  swapped within a week are matched the way they were run (changed 2026-10-14: was same day only). Rest days
  take nothing. Matching never crosses weeks; a session done in another week is linked by hand.
- Session status: done when its activity covers at least 80% of planned_m, partial below that, done with any
  activity when the plan gives no distance, done when marked done by hand. Without an activity: missed once
  the day is over, planned on the day itself and after.
- Day status: rest (only rest, or nothing planned, and no activity), unplanned (nothing but rest planned,
  activities done), planned (today or later with sessions still open), done (every session done), missed
  (every session missed), partial (anything else).
Glucose per activity comes from the stored per-run numbers (app.derive): start, end, change, and whether it
went below the low or started above HIGH_MG_DL.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel

import re

from . import run_metrics as rm
from . import training_settings
from .common import conninfo, require_auth

router = APIRouter(prefix="/api/calendar", dependencies=[Depends(require_auth)])

LOCAL = ZoneInfo("Europe/Madrid")
MAX_DAYS = 62
DONE_SHARE = 0.8
CANDIDATE_DAYS = 3


def plan_sport(sport: str, sub_sport: str | None = None) -> str:
    """The plan's sport for an activity's FIT sport."""
    if sport in ("running", "swimming", "cycling"):
        return sport
    if sport in ("training", "fitness_equipment"):
        return "strength"
    return "other"


# The pace zone a planned run's title asks for (step 9, change 3), checked in this order. Sessions run by
# effort (hill sprints, reps, VO2, strides, time trials, races other than the half) have no pace target.
TITLE_ZONE = [
    (r"\bthreshold\b", "threshold"),
    (r"\btempo\b", "tempo"),
    (r"\bsteady\b", "steady"),
    (r"\brace pace\b|\bhalf marathon\b", "race"),
    (r"\blong\b", "long"),
    (r"\beasy\b|\bshakeout\b|\brecovery\b", "easy"),
]


def pace_zone_of(title: str) -> str | None:
    for pattern, zone in TITLE_ZONE:
        if re.search(pattern, title, re.IGNORECASE):
            return zone
    return None


def target(s: dict, zones: dict | None) -> dict | None:
    """The pace target of a planned run from Settings > Training, or None."""
    if s["sport"] != "running" or not zones:
        return None
    zone = pace_zone_of(s["title"])
    if zone is None or zone not in zones:
        return None
    fast, slow = zones[zone]
    return {"zone": zone, "name": training_settings.PACE_ZONES[zone], "from_s": fast, "to_s": slow}


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# Within-week matching (agreed 2026-10-14). Costs in thousandths, so ties are exact and the result repeatable.
MISFIT_CAP = 1000          # an activity's misfit to a session: |done - planned| / planned, capped at 1
DAY_COST = 250             # per day between the session's day and the activity's
MOVE_MAX_MISFIT = 500      # another day is only considered when the distance fits within 50 %
UNMATCHED = 1010           # a session left without an activity: worse than any allowed pair
MAX_SEARCH = 12            # more activities than this in one week and sport: same-day only (never in practice)


def _misfit(s: dict, a: dict) -> int:
    if not s["planned_m"]:
        return 0
    p = float(s["planned_m"])
    return min(MISFIT_CAP, round(abs((a["distance_m"] or 0) - p) / p * 1000))


def _pair_cost(s: dict, a: dict) -> int | None:
    days = abs((a["day"] - s["day"]).days)
    m = _misfit(s, a)
    if days and m > MOVE_MAX_MISFIT:
        return None
    return m + DAY_COST * days


def _assign(sessions: list[dict], activities: list[dict]) -> list[int | None]:
    """The cheapest assignment of activities to sessions (one each): per session, an index into activities or
    None. Exhaustive search with memo: a week has a handful of sessions and activities per sport."""
    costs = [[_pair_cost(s, a) for a in activities] for s in sessions]
    memo: dict[tuple[int, int], tuple[int, tuple]] = {}

    def best(i: int, used: int) -> tuple[int, tuple]:
        if i == len(sessions):
            return 0, ()
        key = (i, used)
        if key not in memo:
            c, rest = best(i + 1, used)
            option = (c + UNMATCHED, (None,) + rest)
            for j, cost in enumerate(costs[i]):
                if cost is not None and not used >> j & 1:
                    c, rest = best(i + 1, used | 1 << j)
                    if c + cost < option[0]:
                        option = (c + cost, (j,) + rest)
            memo[key] = option
        return memo[key]

    return list(best(0, 0)[1])


def match(sessions: list[dict], activities: list[dict], links: dict[int, dict]) -> dict[int, tuple[int, str]]:
    """{session id: (activity id, 'manual' | 'auto' | 'week')}. sessions: id, day, sport, start_ts, planned_m;
    activities: id, day, sport (the plan's), distance_m, start; links: {session id: {kind, activity_id}}.

    A link set by hand wins. Then, per week (Monday to Sunday) and sport, the open sessions and the free
    activities are paired at the lowest total cost: how far the distance is from the plan (relative, capped)
    plus DAY_COST for every day between them. A pair on another day must fit the planned distance within 50 %;
    a session left without an activity costs more than any allowed pair, so as many sessions as possible are
    done. So a swapped week (the long run on Saturday and the easy run on Sunday) is matched the way it was run,
    while two sessions of about the same distance stay on their own days. 'week' marks a pair on different days."""
    by_id = {a["id"]: a for a in activities}
    out: dict[int, tuple[int, str]] = {}
    for s in sessions:
        link = links.get(s["id"])
        if link and link["kind"] == "activity" and link["activity_id"] in by_id:
            out[s["id"]] = (link["activity_id"], "manual")
    # Activities linked by hand are taken even when their session is outside the range read.
    used = {lk["activity_id"] for lk in links.values() if lk["kind"] == "activity"}

    groups: dict[tuple[date, str], tuple[list, list]] = {}
    order = sorted(sessions, key=lambda s: (s["day"], s["start_ts"] or datetime.max.replace(tzinfo=LOCAL), s["id"]))
    for s in order:
        if s["id"] not in links and s["sport"] != "rest":
            groups.setdefault((monday(s["day"]), s["sport"]), ([], []))[0].append(s)
    # Longest first, so equal costs (sessions without a planned distance) go to the longest activity.
    for a in sorted(activities, key=lambda a: (-(a["distance_m"] or 0), a["start"], a["id"])):
        key = (monday(a["day"]), a["sport"])
        if a["id"] not in used and key in groups:
            groups[key][1].append(a)

    for ss, aa in groups.values():
        if len(aa) > MAX_SEARCH:  # never in practice; keep it bounded: same day, longest first
            free = list(aa)
            for s in ss:
                options = [a for a in free if a["day"] == s["day"]]
                if options:
                    a = max(options, key=lambda a: (a["distance_m"] or 0, -a["start"]))
                    free.remove(a)
                    out[s["id"]] = (a["id"], "auto")
            continue
        for s, j in zip(ss, _assign(ss, aa)):
            if j is not None:
                a = aa[j]
                out[s["id"]] = (a["id"], "auto" if a["day"] == s["day"] else "week")
    return out


def session_status(s: dict, activity: dict | None, link: dict | None, today: date) -> str:
    if s["sport"] == "rest":
        return "rest"
    if link and link["kind"] == "done":
        return "done"
    if activity is not None:
        if s["planned_m"] and (activity["distance_m"] or 0) < DONE_SHARE * float(s["planned_m"]):
            return "partial"
        return "done"
    return "missed" if s["day"] < today else "planned"


def day_status(statuses: list[str], unplanned: int, day: date, today: date) -> str:
    work = [x for x in statuses if x != "rest"]
    if not work:
        return "unplanned" if unplanned else "rest"
    if "planned" in work and day >= today:
        return "done" if all(x == "done" for x in work) else "planned"
    if all(x == "done" for x in work):
        return "done"
    if all(x == "missed" for x in work):
        return "missed"
    return "partial"


def build(start: date, end: date, today: date, sessions: list[dict], activities: list[dict], links: dict[int, dict],
          zones: dict | None = None) -> dict:
    """The calendar for start..end. activities may include ones outside the range that are linked by hand."""
    assigned = match(sessions, activities, links)
    act = {a["id"]: a for a in activities}
    used = {aid for aid, _ in assigned.values()} | {lk["activity_id"] for lk in links.values() if lk["kind"] == "activity"}
    days = []
    d = start
    while d <= end:
        rows = []
        for s in (x for x in sessions if x["day"] == d):
            aid, how = assigned.get(s["id"], (None, None))
            link = links.get(s["id"])
            rows.append({
                "id": s["id"], "calendar": s["calendar"], "sport": s["sport"], "title": s["title"],
                "description": s["description"], "planned_m": None if s["planned_m"] is None else float(s["planned_m"]),
                "time": s["start_ts"].astimezone(LOCAL).strftime("%H:%M") if s["start_ts"] else None,
                "status": session_status(s, act.get(aid) if aid else None, link, today),
                "activity_id": aid,
                "link": link["kind"] if link else how,
                "activity_day": act[aid]["day"].isoformat() if aid and act[aid]["day"] != d else None,
                "target": target(s, zones),
            })
        rows.sort(key=lambda r: (r["sport"] == "rest", r["time"] or "99", r["id"]))
        unplanned = [a["id"] for a in sorted(activities, key=lambda a: a["start"]) if a["day"] == d and a["id"] not in used]
        days.append({"day": d.isoformat(), "status": day_status([r["status"] for r in rows], len(unplanned), d, today),
                     "sessions": rows, "unplanned": unplanned})
        d += timedelta(days=1)
    shown = {r["activity_id"] for x in days for r in x["sessions"] if r["activity_id"]} | {i for x in days for i in x["unplanned"]}
    return {"today": today.isoformat(), "start": start.isoformat(), "end": end.isoformat(), "days": days,
            "activities": {str(i): _public(act[i]) for i in sorted(shown)}}


def _public(a: dict) -> dict:
    g0 = a.get("glucose_start")
    return {
        "id": a["id"], "day": a["day"].isoformat(), "sport": a["fit_sport"], "sub_sport": a.get("sub_sport"),
        "start": a["start"], "distance_m": a["distance_m"], "moving_s": a.get("moving_s"), "elapsed_s": a.get("elapsed_s"),
        "avg_hr": a.get("avg_hr"),
        "glucose": None if g0 is None else {
            "start": g0, "end": a.get("glucose_end"), "delta": a.get("delta"),
            "went_low": bool(a.get("min_below_low")), "high_start": g0 > rm.HIGH_MG_DL,
        },
    }


ACTIVITY_SQL = """
    SELECT a.id, a.sport AS fit_sport, a.sub_sport, extract(epoch FROM a.start_ts)::float8 AS start,
           (a.start_ts AT TIME ZONE 'Europe/Madrid')::date AS day,
           a.distance_m::float8 AS distance_m, a.moving_s::float8 AS moving_s, a.elapsed_s::float8 AS elapsed_s, a.avg_hr,
           g.glucose_start, g.glucose_end, g.delta, g.min_below_low
    FROM activity a LEFT JOIN run_glucose_metrics g ON g.activity_id = a.id
"""


def _activities(conn, where: str, params) -> list[dict]:
    rows = conn.execute(f"{ACTIVITY_SQL} WHERE {where}", params).fetchall()
    return [{**r, "sport": plan_sport(r["fit_sport"], r["sub_sport"])} for r in rows]


def load(conn, start: date, end: date, today: date) -> dict:
    """Reads whole weeks around start..end (matching works per week), returns start..end."""
    a, b = monday(start), monday(end) + timedelta(days=6)
    sessions = conn.execute(
        "SELECT id, calendar, day, start_ts, sport, title, description, planned_m FROM planned_session "
        "WHERE removed_at IS NULL AND day BETWEEN %s AND %s",
        (a, b),
    ).fetchall()
    links = {r["planned_session_id"]: r for r in conn.execute("SELECT planned_session_id, kind, activity_id FROM plan_link")}
    ids = {s["id"] for s in sessions}
    activities = _activities(
        conn,
        "(a.start_ts AT TIME ZONE 'Europe/Madrid')::date BETWEEN %(a)s AND %(b)s OR a.id = ANY(%(linked)s)",
        {"a": a, "b": b, "linked": [lk["activity_id"] for sid, lk in links.items() if lk["kind"] == "activity" and sid in ids]},
    )
    ts = training_settings.current(conn)
    return build(start, end, today, sessions, activities, links, ts["pace_zones"] if ts else None)


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def _today() -> date:
    return datetime.now(LOCAL).date()


@router.get("")
def calendar(start: Annotated[date, Query()], end: Annotated[date, Query()]):
    if end < start:
        raise HTTPException(status_code=422, detail="end must not be before start")
    if (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"at most {MAX_DAYS} days at a time")
    with _connect() as conn:
        return load(conn, start, end, _today())


def _session(conn, sid: int) -> dict:
    s = conn.execute("SELECT id, day, sport FROM planned_session WHERE id = %s AND removed_at IS NULL", (sid,)).fetchone()
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@router.get("/sessions/{sid}/candidates")
def candidates(sid: int):
    with _connect() as conn:
        s = _session(conn, sid)
        rows = _activities(
            conn,
            "(a.start_ts AT TIME ZONE 'Europe/Madrid')::date BETWEEN %(a)s AND %(b)s "
            "AND NOT EXISTS (SELECT 1 FROM plan_link l WHERE l.activity_id = a.id AND l.planned_session_id <> %(sid)s) "
            "ORDER BY a.start_ts",
            {"a": s["day"] - timedelta(days=CANDIDATE_DAYS), "b": s["day"] + timedelta(days=CANDIDATE_DAYS), "sid": sid},
        )
    # Same sport first, then by how close to the planned day.
    rows.sort(key=lambda a: (a["sport"] != s["sport"], abs((a["day"] - s["day"]).days), a["start"]))
    return [_public(a) for a in rows]


class LinkIn(BaseModel):
    kind: Literal["activity", "done", "none"]
    activity_id: int | None = None


@router.put("/sessions/{sid}/link", status_code=204)
def set_link(sid: int, body: LinkIn):
    if (body.kind == "activity") != (body.activity_id is not None):
        raise HTTPException(status_code=422, detail="activity_id is needed for kind 'activity', and only for it")
    with _connect() as conn:
        s = _session(conn, sid)
        if s["sport"] == "rest":
            raise HTTPException(status_code=422, detail="a rest day has nothing to link")
        if body.activity_id is not None:
            if conn.execute("SELECT 1 FROM activity WHERE id = %s", (body.activity_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="activity not found")
            # An activity does one session: moving it here frees the session it did before.
            conn.execute("DELETE FROM plan_link WHERE activity_id = %s AND planned_session_id <> %s", (body.activity_id, sid))
        conn.execute(
            "INSERT INTO plan_link (planned_session_id, kind, activity_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (planned_session_id) DO UPDATE SET kind = EXCLUDED.kind, activity_id = EXCLUDED.activity_id, created_at = now()",
            (sid, body.kind, body.activity_id),
        )


@router.delete("/sessions/{sid}/link", status_code=204)
def clear_link(sid: int):
    with _connect() as conn:
        _session(conn, sid)
        conn.execute("DELETE FROM plan_link WHERE planned_session_id = %s", (sid,))

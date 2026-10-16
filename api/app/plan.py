"""Training plan (roadmap step 9, change 1): import of the plan's .ics calendars into planned_session.

POST /api/plan/ics?name=<file name>   the .ics file's bytes as the request body. Archived first, under
                                      RAW_DIR/plan/<first 2 hex of sha256>/<sha256>.ics, then parsed (app.ics).
GET  /api/plan/calendars              every imported calendar: sessions per sport, first and last day, the
                                      last import and what it changed

A calendar is known by its X-WR-CALNAME (else the file name). Importing it again updates its sessions by UID;
a session no longer in the file is marked removed (removed_at) and comes back if a later file has it again.
Rebuild from the raw files: `python -m app.plan --reimport` (the newest file of every calendar).

What each session is, read from the file:
  sport       CATEGORIES first (RUN, RACE, SWIM, GYM, BIKE, REST...), else words in the title
  day         the local day (Europe/Madrid) it starts on
  planned_m   the distance to cover, when the text gives one: running and cycling "About 9 km" in the
              description, else "6 km" or "5K" in the title; swimming "Total: about 1150 m". None otherwise
              (gym, rest, and sessions planned by time only). The calendar uses it for done or partial.
Target paces in the text are not used: they come from Settings > Training (change 3).
"""

import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from psycopg.rows import dict_row

from . import ics
from .common import conninfo, require_auth

router = APIRouter(prefix="/api/plan", dependencies=[Depends(require_auth)])

LOCAL = ZoneInfo("Europe/Madrid")
MAX_BYTES = 2 * 1024 * 1024

CATEGORY_SPORT = {
    "RUN": "running", "RUNNING": "running", "RACE": "running",
    "SWIM": "swimming", "SWIMMING": "swimming",
    "BIKE": "cycling", "RIDE": "cycling", "CYCLING": "cycling",
    "GYM": "strength", "STRENGTH": "strength",
    "REST": "rest",
}
# Checked in this order against the title when no category says it.
TITLE_SPORT = [
    (r"\brest\b", "rest"),
    (r"\bswim", "swimming"),
    (r"\b(?:gym|strength)\b", "strength"),
    (r"\b(?:bike|ride|cycl)", "cycling"),
    (r"\b(?:run|race|easy|long|tempo|threshold|reps|strides|jog|shakeout|marathon)\b|\d\s*km\b|\b\d+K\b", "running"),
]


def sport_of(categories: list[str], title: str) -> str:
    for c in categories:
        if c in CATEGORY_SPORT:
            return CATEGORY_SPORT[c]
    for pattern, sport in TITLE_SPORT:
        if re.search(pattern, title, re.IGNORECASE):
            return sport
    return "other"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def planned_m(sport: str, title: str, description: str | None) -> float | None:
    """The distance to cover in metres, when the session's text gives one (see the module docstring)."""
    text = description or ""
    if sport in ("running", "cycling"):
        m = re.search(r"\babout\s+(\d+(?:[.,]\d+)?)\s*km\b", text, re.IGNORECASE)
        m = m or re.search(r"(\d+(?:[.,]\d+)?)\s*km\b", title, re.IGNORECASE)
        m = m or re.search(r"\b(\d+(?:[.,]\d+)?)K\b", title)
        return round(_num(m.group(1)) * 1000, 1) if m else None
    if sport == "swimming":
        m = re.search(r"\btotal:?\s*(?:about\s+)?(\d{2,5})\s*m\b", text, re.IGNORECASE)
        return float(m.group(1)) if m else None
    return None


def raw_root() -> Path:
    return Path(os.environ.get("RAW_DIR", "/raw"))


def archive(data: bytes) -> tuple[str, str]:
    """Store the bytes as received; returns (path relative to RAW_DIR, sha256). Raises OSError."""
    sha = hashlib.sha256(data).hexdigest()
    rel = f"plan/{sha[:2]}/{sha}.ics"
    path = raw_root() / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return rel, sha


def _row(e: ics.Event) -> dict:
    sport = sport_of(e.categories, e.summary)
    if e.all_day:
        day, start, end = e.start, None, None
    else:
        day, start = e.start.astimezone(LOCAL).date(), e.start
        end = e.end if isinstance(e.end, datetime) else None
    return {"uid": e.uid, "day": day, "start_ts": start, "end_ts": end, "sport": sport,
            "title": e.summary or "(no title)", "description": e.description,
            "planned_m": planned_m(sport, e.summary, e.description)}


def import_ics(conn, data: bytes, file_name: str | None = None) -> dict:
    """Archive, parse and store one calendar file. Raises ics.IcsProblem (bad file) or OSError (archive)."""
    rel, sha = archive(data)
    cal = ics.parse(data)
    name = cal.name or (Path(file_name).stem.strip() if file_name else "")
    if not name:
        raise ics.IcsProblem("the calendar has no name (X-WR-CALNAME) and the file name is missing")
    rows = [_row(e) for e in cal.events]

    added = updated = 0
    for r in rows:
        out = conn.execute(
            """INSERT INTO planned_session (calendar, uid, day, start_ts, end_ts, sport, title, description, planned_m)
               VALUES (%(calendar)s, %(uid)s, %(day)s, %(start_ts)s, %(end_ts)s, %(sport)s, %(title)s, %(description)s, %(planned_m)s)
               ON CONFLICT (uid) DO UPDATE SET
                   calendar = EXCLUDED.calendar, day = EXCLUDED.day, start_ts = EXCLUDED.start_ts, end_ts = EXCLUDED.end_ts,
                   sport = EXCLUDED.sport, title = EXCLUDED.title, description = EXCLUDED.description,
                   planned_m = EXCLUDED.planned_m, removed_at = NULL, updated_at = now()
               WHERE (planned_session.calendar, planned_session.day, planned_session.start_ts, planned_session.end_ts,
                      planned_session.sport, planned_session.title, planned_session.description, planned_session.planned_m)
                     IS DISTINCT FROM
                     (EXCLUDED.calendar, EXCLUDED.day, EXCLUDED.start_ts, EXCLUDED.end_ts,
                      EXCLUDED.sport, EXCLUDED.title, EXCLUDED.description, EXCLUDED.planned_m)
                  OR planned_session.removed_at IS NOT NULL
               RETURNING (xmax = 0) AS inserted""",
            {**r, "calendar": name},
        ).fetchone()
        if out is not None:
            added += out["inserted"]
            updated += not out["inserted"]
    removed = conn.execute(
        "UPDATE planned_session SET removed_at = now() WHERE calendar = %s AND removed_at IS NULL AND NOT (uid = ANY(%s))",
        (name, [r["uid"] for r in rows]),
    ).rowcount
    unchanged = len(rows) - added - updated
    conn.execute(
        "INSERT INTO plan_import (calendar, raw_path, sha256, added, updated, unchanged, removed) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (name, rel, sha, added, updated, unchanged, removed),
    )
    conn.commit()
    return {"calendar": name, "sessions": len(rows), "added": added, "updated": updated, "unchanged": unchanged,
            "removed": removed}


def calendars(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT s.calendar,
                  count(*) FILTER (WHERE s.removed_at IS NULL) AS sessions,
                  count(*) FILTER (WHERE s.removed_at IS NOT NULL) AS removed,
                  min(s.day) FILTER (WHERE s.removed_at IS NULL) AS first_day,
                  max(s.day) FILTER (WHERE s.removed_at IS NULL) AS last_day,
                  (SELECT json_object_agg(sport, n) FROM (
                       SELECT sport, count(*) AS n FROM planned_session x
                       WHERE x.calendar = s.calendar AND x.removed_at IS NULL GROUP BY sport) c) AS by_sport,
                  (SELECT row_to_json(i) FROM (
                       SELECT imported_at, added, updated, unchanged, removed FROM plan_import p
                       WHERE p.calendar = s.calendar ORDER BY imported_at DESC, id DESC LIMIT 1) i) AS last_import
           FROM planned_session s GROUP BY s.calendar ORDER BY s.calendar"""
    ).fetchall()
    out = []
    for r in rows:
        li = r["last_import"]
        out.append({
            "calendar": r["calendar"], "sessions": r["sessions"], "removed": r["removed"],
            "first_day": r["first_day"].isoformat() if r["first_day"] else None,
            "last_day": r["last_day"].isoformat() if r["last_day"] else None,
            "by_sport": r["by_sport"] or {},
            "last_import": None if li is None else {
                **{k: li[k] for k in ("added", "updated", "unchanged", "removed")},
                "at": datetime.fromisoformat(li["imported_at"]).timestamp(),
            },
        })
    return out


def _connect():
    return psycopg.connect(conninfo(), row_factory=dict_row)


@router.post("/ics", status_code=201)
async def upload_ics(request: Request, name: Annotated[str | None, Query(max_length=200)] = None):
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 2 MB")
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 2 MB")

    def work():
        with _connect() as conn:
            return import_ics(conn, data, name)

    try:
        return await run_in_threadpool(work)
    except ics.IcsProblem as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=503, detail="the raw archive is not writable: nothing was imported") from e


@router.get("/calendars")
def list_calendars():
    with _connect() as conn:
        return calendars(conn)


def reimport() -> list[dict]:
    """Import again the newest raw file of every calendar (after a change to how sessions are read)."""
    with _connect() as conn:
        latest = conn.execute(
            "SELECT DISTINCT ON (calendar) calendar, raw_path FROM plan_import ORDER BY calendar, imported_at DESC, id DESC"
        ).fetchall()
        return [import_ics(conn, (raw_root() / r["raw_path"]).read_bytes(), r["calendar"]) for r in latest]


if __name__ == "__main__":
    if sys.argv[1:] != ["--reimport"]:
        sys.exit("usage: python -m app.plan --reimport")
    for result in reimport():
        print(result)

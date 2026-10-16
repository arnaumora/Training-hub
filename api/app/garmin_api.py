"""Garmin sync status and "Sync now" for the app (roadmap step 6). The sync itself runs in the garmin service
(app.garmin_sync); the API only reads its bookkeeping and queues requests. The API never sees the tokens.

GET  /api/garmin/status  last success, last error, whether a login is needed, the last runs, a pending request
POST /api/garmin/sync    queue a run (a pending one is reused; refused within MIN_GAP_S of the last start)
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from .common import conninfo, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

MIN_GAP_S = 120  # Garmin does not like being hammered; one run every 2 minutes at most from the button
STALE_H = 36  # /health/garmin fails after this long without a successful sync


def _ts(v):
    return None if v is None else v.timestamp()


def status_row(conn) -> dict:
    s = conn.execute(
        "SELECT last_success_at, last_attempt_at, last_error_at, last_error, consecutive_failures "
        "FROM collector_status WHERE name = 'garmin'"
    ).fetchone()
    last = conn.execute(
        "SELECT login_needed FROM garmin_sync_run WHERE finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    return {
        "last_success": _ts(s["last_success_at"]) if s else None,
        "last_attempt": _ts(s["last_attempt_at"]) if s else None,
        "last_error": s["last_error"] if s and s["consecutive_failures"] else None,
        "consecutive_failures": s["consecutive_failures"] if s else 0,
        "login_needed": bool(last and last["login_needed"]),
    }


@router.get("/garmin/status")
def garmin_status():
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        out = status_row(conn)
        runs = conn.execute(
            "SELECT id, trigger, requested_at, started_at, finished_at, ok, login_needed, checked, imported, skipped, error "
            "FROM garmin_sync_run ORDER BY requested_at DESC LIMIT 5"
        ).fetchall()
        out["activities"] = conn.execute(
            "SELECT count(*) AS n FROM activity WHERE garmin_activity_id IS NOT NULL"
        ).fetchone()["n"]
    out["runs"] = [
        {**r, "requested_at": _ts(r["requested_at"]), "started_at": _ts(r["started_at"]), "finished_at": _ts(r["finished_at"])}
        for r in runs
    ]
    out["busy"] = any(r["finished_at"] is None for r in runs)
    return out


@router.post("/garmin/sync", status_code=202)
def garmin_sync_now():
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        pending = conn.execute("SELECT id FROM garmin_sync_run WHERE started_at IS NULL ORDER BY id LIMIT 1").fetchone()
        if pending:
            return {"queued": pending["id"]}
        recent = conn.execute(
            "SELECT extract(epoch FROM now() - max(started_at))::int AS age, bool_or(finished_at IS NULL AND started_at IS NOT NULL) AS running "
            # A run that stopped for a missing login never talked to Garmin: it does not count for the gap.
            "FROM garmin_sync_run WHERE started_at > now() - make_interval(secs => %s) AND NOT login_needed",
            (MIN_GAP_S,),
        ).fetchone()
        if recent["age"] is not None:
            raise HTTPException(
                status_code=429,
                detail="a sync is running" if recent["running"] else "a sync just ran: try again in a couple of minutes",
            )
        run_id = conn.execute("INSERT INTO garmin_sync_run (trigger) VALUES ('request') RETURNING id").fetchone()["id"]
    return {"queued": run_id}

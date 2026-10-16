"""Training Hub API.

/health and /health/glucose (unauthenticated, for monitoring), single-user login, the glucose
series for the chart, the insulin and carbs log (log.py) and Settings > Insulin (insulin_settings.py).
"""

import logging
import os
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import activities, ai_reports, barcode, calc_api, compare, connector_api, foods, garmin_api, insulin_settings, own_foods, plan, plan_calendar, training_settings, week
from . import log as insulin_carbs_log
from .auth import LoginThrottle, load_password_hash, verify_password
from .common import conninfo as _conninfo
from .common import require_auth

log = logging.getLogger("traininghub.api")

SESSION_DAYS = 90


def _session_secret() -> str:
    value = os.environ.get("SESSION_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("SESSION_SECRET is missing or shorter than 32 characters")
    return value


# Both checks run at import, so the container fails loudly instead of running unprotected.
PASSWORD_HASH = load_password_hash()

# No interactive docs: this is a private app, not a public API.
app = FastAPI(title="Training Hub API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="th_session",
    max_age=SESSION_DAYS * 24 * 3600,
    same_site="lax",
    # The app is only meant to be reached over HTTPS through Nginx Proxy Manager.
    https_only=True,
)

throttle = LoginThrottle()


@app.get("/health")
def health():
    try:
        with psycopg.connect(_conninfo()) as conn:
            conn.execute("select 1")
    except Exception:
        # Full error goes to the container log; the response stays generic.
        log.exception("health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "db": "unreachable"})
    return {"status": "ok", "db": "ok"}


# The collector polls every 60 s and backs off to at most 15 min on errors; no success within
# 5 min means it is failing, not just slow.
COLLECTOR_STALE_S = 300


@app.get("/health/glucose")
def health_glucose():
    """Collector health for monitoring. Unauthenticated, so it returns ages and counts only, never values.

    200 while the LibreLinkUp collector has succeeded within COLLECTOR_STALE_S. Old readings alone do NOT
    make it fail: no new readings usually just means LibreLink is closed on the phone, which is not an outage.
    """
    try:
        with psycopg.connect(_conninfo()) as conn:
            status = conn.execute(
                "SELECT extract(epoch FROM now() - last_success_at)::int, consecutive_failures "
                "FROM collector_status WHERE name = 'llu'"
            ).fetchone()
            newest, newest_live = conn.execute(
                "SELECT extract(epoch FROM now() - max(ts))::int, "
                "extract(epoch FROM now() - max(ts) FILTER (WHERE source = 'llu_live'))::int "
                "FROM glucose_reading"
            ).fetchone()
    except Exception:
        log.exception("glucose health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "db": "unreachable"})

    success_age, failures = status if status else (None, 0)
    body = {
        "collector": {"last_success_age_s": success_age, "consecutive_failures": failures},
        "readings": {"newest_age_s": newest, "newest_live_age_s": newest_live},
    }
    if success_age is None:
        return JSONResponse(status_code=503, content={"status": "collector_never_succeeded", **body})
    if success_age > COLLECTOR_STALE_S:
        return JSONResponse(status_code=503, content={"status": "collector_failing", **body})
    return {"status": "ok", **body}


@app.get("/health/garmin")
def health_garmin():
    """Garmin sync health for monitoring (ops/garmin-watch.sh). Unauthenticated: ages and flags only.

    Fails when the sync has not succeeded for garmin_api.STALE_H hours (or never), and at once when the last
    run stopped because a login is needed (tokens missing or rejected), since that never fixes itself.
    """
    try:
        with psycopg.connect(_conninfo(), row_factory=dict_row) as conn:
            s = garmin_api.status_row(conn)
            age = conn.execute(
                "SELECT extract(epoch FROM now() - last_success_at)::int AS age FROM collector_status WHERE name = 'garmin'"
            ).fetchone()
    except Exception:
        log.exception("garmin health check: database unreachable")
        return JSONResponse(status_code=503, content={"status": "error", "db": "unreachable"})
    body = {"last_success_age_s": age["age"] if age else None, "consecutive_failures": s["consecutive_failures"]}
    if s["login_needed"]:
        return JSONResponse(status_code=503, content={"status": "login_needed", **body})
    if body["last_success_age_s"] is None:
        return JSONResponse(status_code=503, content={"status": "never_succeeded", **body})
    if body["last_success_age_s"] > garmin_api.STALE_H * 3600:
        return JSONResponse(status_code=503, content={"status": "stale", **body})
    return {"status": "ok", **body}


class LoginBody(BaseModel):
    password: str


@app.post("/api/login", status_code=204)
def login(body: LoginBody, request: Request) -> Response:
    if throttle.locked():
        raise HTTPException(status_code=429, detail="too many failed attempts, try again later")
    if not verify_password(PASSWORD_HASH, body.password):
        throttle.record_failure()
        log.warning("login: wrong password")
        raise HTTPException(status_code=401, detail="wrong password")
    throttle.record_success()
    request.session.clear()
    request.session["auth"] = True
    request.session["since"] = int(time.time())
    return Response(status_code=204)


@app.post("/api/logout", status_code=204)
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)


@app.get("/api/me", dependencies=[Depends(require_auth)])
def me():
    return {"authenticated": True}


@app.get("/api/glucose", dependencies=[Depends(require_auth)])
def glucose(hours: int = Query(24, ge=1, le=72)):
    """Readings for the last `hours`, oldest first, for the glucose chart.

    points: [epoch seconds of the minute (UTC), mg/dL, live?]. live = one-minute reading from the phone;
    not live = 15-minute history backfilled when LibreLink was not running. Coverage counts live
    minutes against all minutes in the window, which shows how much of the time LibreLink was running.
    """
    with psycopg.connect(_conninfo()) as conn:
        now_epoch = conn.execute("SELECT extract(epoch FROM now())::bigint").fetchone()[0]
        rows = conn.execute(
            "SELECT extract(epoch FROM minute)::bigint, mg_dl, source = 'llu_live' "
            "FROM glucose_reading WHERE minute >= now() - make_interval(hours => %s) ORDER BY minute",
            (hours,),
        ).fetchall()
        latest = conn.execute(
            "SELECT extract(epoch FROM ts)::bigint, mg_dl, trend, source = 'llu_live' "
            "FROM glucose_reading ORDER BY minute DESC LIMIT 1"
        ).fetchone()
        ts = training_settings.current(conn)
    return {
        "from": now_epoch - hours * 3600,
        "to": now_epoch,
        "points": [[t, v, live] for t, v, live in rows],
        "latest": (
            {"t": latest[0], "mg_dl": latest[1], "trend": latest[2], "live": latest[3]} if latest else None
        ),
        "coverage": {"live_minutes": sum(1 for r in rows if r[2]), "minutes": hours * 60},
        # The target band, from Settings > Training (70-180 until saved otherwise).
        "target": {"low": ts["glucose_low"], "high": ts["glucose_high"]} if ts else {"low": 70, "high": 180},
    }


app.include_router(insulin_carbs_log.router)
app.include_router(insulin_settings.router)
app.include_router(calc_api.router)
app.include_router(foods.router)
app.include_router(compare.router)
app.include_router(ai_reports.router)
app.include_router(week.router)
app.include_router(connector_api.router)
app.include_router(own_foods.router)  # after foods: its /foods/{id} must not catch /foods/frequent
app.include_router(barcode.router)
app.include_router(activities.router)
app.include_router(garmin_api.router)
app.include_router(training_settings.router)
app.include_router(plan.router)
app.include_router(plan_calendar.router)


# The built SvelteKit app. Mounted last so every /api route and /health above take precedence.
# The static files hold no data: the login screen must load before login, and all data comes from /api.
WEB_DIR = Path(os.environ.get("WEB_DIR", "/srv/web"))
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

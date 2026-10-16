"""AI reports and Settings > AI (roadmap step 8): a report per run (change 1) and a summary per week (change 2).

GET  /api/settings/ai                 current settings, which providers have a key, tokens used (30 days)
POST /api/settings/ai                 save a new version
GET  /api/activities/{id}/report      the newest report, whether the run's data changed since, the open job
POST /api/activities/{id}/report      queue a report ({"provider": "claude" | "gemini" | null for the default})
GET  /api/weeks/{monday}/report       the same for a week (monday as YYYY-MM-DD)
POST /api/weeks/{monday}/report

Weekly summaries are also queued by the analysis service itself, on Monday from WEEKLY_AT local time, for
the week that just ended (when Settings > AI writes reports by themselves and the week has glucose readings).

Reports are written by the analysis service (app.ai_worker), which works through ai_job: queued here on a
tap, and by app.fit_import after an import when Settings > AI asks for it (AUTO_MAX_AGE: only recent
activities, so the first sync's 30-day backfill does not pay for a dozen reports).

write_report(conn, kind, ref_id, provider) builds the input (app.ai_input or app.week), calls the provider
(app.ai_providers), passes the text through the dose guard (app.ai_guard), and stores the report and the
call. Health rules: the model only interprets numbers code computed; it never produces a dose or changes a
setting, and the guard removes any sentence that suggests one.
"""

import json
import logging
import time
from datetime import date, datetime, time as clock, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import ai_guard, ai_input, ai_providers, week
from .common import conninfo, require_auth

log = logging.getLogger("traininghub.ai")

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

PROMPT_VERSION = "run-5"  # raise with any change to SYSTEM or ai_guidelines.md
WEEK_PROMPT_VERSION = "week-2"  # raise with any change to WEEK_SYSTEM or ai_guidelines.md
WEEKLY_AT = clock(6, 0)  # Monday, local time: the summary of the week before is queued
MAX_ATTEMPTS = 4  # per job; the analysis service waits 2, 4, then 8 min between them
SERVICE_STALE = timedelta(minutes=3)  # no sign of the analysis service for this long: not running
AUTO_MAX_AGE = timedelta(hours=48)
MAX_OUTPUT_TOKENS = 8192
NAMES = {"claude": "Claude", "gemini": "Gemini"}

GUIDELINES = (Path(__file__).with_name("ai_guidelines.md")).read_text(encoding="utf-8")

SYSTEM = """You write the report on one training session for the athlete described in the reference below.

The user message is JSON. Every number in it was computed by code: never recompute one differently and never \
invent a value that is not there. Times are minutes from the start of the activity (negative = before it).

Rules, in order of importance:
1. Never write an insulin dose or amount, or any change to insulin timing, ratios, sensitivity, targets, basal \
or boluses, not even hedged or as an example. If something looks worth raising with the endocrinologist, write \
"Review suggested:" followed by the data, with no proposed change.
2. "flags_must_mention" lists what code found that the athlete must not miss. Mention every one of them, in \
your own words: safety flags in the Summary, the others under Worth noting.
3. Explain, do not recite: say what happened and why it likely happened, using the reference. The athlete \
already sees every number on the page; use a number only to make a point.
4. Say "per the log" for insulin on board, doses and carbs.

Write in English, under 300 words, with these Markdown sections:
## Summary
Two or three sentences: the session, what glucose did and the likely main reason, and any safety flag.
## Glucose
The course before, during and in the 2 h after, and how insulin on board, carbs and intensity explain it.
## Training
Intensity (zones), pacing, HR drift, anything notable in the laps or the series; only what matters.
## Compared with similar runs
What was different from the similar past runs and how it turned out (say n). If there are none, one line.
## Worth noting
Up to three short bullets: the remaining flags, data gaps, any "Review suggested:" item.

Reference:

""" + GUIDELINES

WEEK_SYSTEM = """You write the summary of one training week for the athlete described in the reference below.

The user message is JSON. Every number in it was computed by code: never recompute one differently and never \
invent a value that is not there. Glucose shares are of the time the sensor covered.

Rules, in order of importance:
1. Never write an insulin dose or amount, or any change to insulin timing, ratios, sensitivity, targets, basal \
or boluses, not even hedged or as an example. If something looks worth raising with the endocrinologist, write \
"Review suggested:" followed by the data, with no proposed change.
2. "flags_must_mention" lists what code found that the athlete must not miss. Mention every one of them, in \
your own words: safety flags in "The week", the others under Worth noting.
3. Look for patterns across the sessions and days, not a list of them: what went with falls, rises and lows \
(time of day, insulin on board and carbs per the log, intensity, duration), and the nights after training. \
Say how many sessions a pattern rests on; with fewer than three, call it something to watch, not a pattern.
4. Say "per the log" for insulin and carbs.

Write in English, under 350 words, with these Markdown sections:
## The week
Two or three sentences: the training load, how glucose went, and any safety flag.
## Glucose and training
The patterns, with the sessions or days they rest on.
## Compared with the week before
Load and glucose against the previous week, briefly.
## Worth noting
Up to four short bullets: the remaining flags, data gaps, any "Review suggested:" item.

Reference:

""" + GUIDELINES

# What each kind of job writes: report scope, prompt, prompt version, input builder, the input's label.
KINDS = {
    "run_report": {"scope": "activity", "system": SYSTEM, "version": PROMPT_VERSION,
                   "build": ai_input.build_run, "intro": "The session data"},
    "week_summary": {"scope": "week", "system": WEEK_SYSTEM, "version": WEEK_PROMPT_VERSION,
                     "build": week.build_week, "intro": "The week's data"},
}
SCOPE_KIND = {v["scope"]: k for k, v in KINDS.items()}
LOCAL = ZoneInfo("Europe/Madrid")


# --- settings ------------------------------------------------------------------------------------------


class AiIn(BaseModel):
    default_provider: Literal["claude", "gemini"]
    claude_model: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")]
    gemini_model: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")]
    auto_report: bool


SETTINGS = "version, created_at, default_provider, claude_model, gemini_model, auto_report"


def current(conn) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        r = cur.execute(f"SELECT {SETTINGS} FROM ai_settings ORDER BY version DESC LIMIT 1").fetchone()
    return {**r, "created_at": r["created_at"].timestamp()}


def service(conn) -> dict:
    """What the analysis service last said about itself (it alone holds the API keys)."""
    r = conn.execute("SELECT seen_at, claude, gemini, seen_at > now() - %s AS running FROM ai_service", (SERVICE_STALE,)).fetchone()
    if r is None:
        return {"running": False, "seen_at": None, "configured": {p: False for p in NAMES}}
    return {"running": r["running"], "seen_at": r["seen_at"].timestamp(), "configured": {p: r[p] for p in NAMES}}


@router.get("/settings/ai")
def get_ai_settings():
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        usage = conn.execute(
            "SELECT provider, count(*) AS calls, count(*) FILTER (WHERE NOT ok) AS failed, "
            "coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens "
            "FROM ai_call WHERE at > now() - interval '30 days' GROUP BY provider ORDER BY provider"
        ).fetchall()
        return {
            "current": current(conn),
            "service": service(conn),
            "usage_30d": usage,
        }


@router.post("/settings/ai", status_code=201)
def save_ai_settings(body: AiIn):
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        cur = current(conn)
        if all(cur[k] == v for k, v in body.model_dump().items()):
            raise HTTPException(status_code=409, detail="nothing changed")
        conn.execute(
            "INSERT INTO ai_settings (default_provider, claude_model, gemini_model, auto_report) "
            "VALUES (%(default_provider)s, %(claude_model)s, %(gemini_model)s, %(auto_report)s)",
            body.model_dump(),
        )
        return current(conn)


# --- jobs ----------------------------------------------------------------------------------------------


def queue(conn, ref_id: int, provider: str | None, trigger: str, kind: str = "run_report") -> int:
    return conn.execute(
        "INSERT INTO ai_job (kind, ref_id, provider, trigger) VALUES (%s, %s, %s, %s) RETURNING id",
        (kind, ref_id, provider, trigger),
    ).fetchone()["id"]


def queue_weekly(conn, now: datetime | None = None) -> int | None:
    """From Monday WEEKLY_AT (local), the summary of the week that just ended, once, when Settings > AI writes
    reports by themselves and that week has glucose readings. Called by the analysis service."""
    now = (now or datetime.now(LOCAL)).astimezone(LOCAL)
    this_monday = week.monday_of(now.date())
    if now.time() < WEEKLY_AT and now.date() == this_monday:
        return None
    if not current(conn)["auto_report"]:
        return None
    ref = week.ref_of(this_monday - timedelta(days=7))
    if conn.execute("SELECT 1 FROM ai_job WHERE kind = 'week_summary' AND ref_id = %s", (ref,)).fetchone():
        return None
    if conn.execute("SELECT 1 FROM ai_report WHERE scope = 'week' AND ref_id = %s", (ref,)).fetchone():
        return None
    start, end = week.bounds(this_monday - timedelta(days=7))
    # Only weeks with glucose: a summary of the training load alone is not worth a call.
    if not conn.execute(
        "SELECT EXISTS (SELECT 1 FROM glucose_reading WHERE minute >= %s AND minute < %s) AS any", (start, end)
    ).fetchone()["any"]:
        return None
    job = queue(conn, ref, None, "schedule", "week_summary")
    conn.commit()
    return job


def queue_after_import(conn, activities: list[dict]) -> int:
    """Called by app.fit_import for the activities an import created (conn with dict_row, caller commits)."""
    if not activities or not current(conn)["auto_report"]:
        return 0
    n = 0
    for a in activities:
        recent = conn.execute("SELECT end_ts > now() - %s AS recent FROM activity WHERE id = %s", (AUTO_MAX_AGE, a["id"])).fetchone()
        if recent and recent["recent"]:
            queue(conn, a["id"], None, "import")
            n += 1
    return n


def write_report(conn, kind: str, ref_id: int, provider: str | None) -> dict:
    """Build, call, guard, store. conn with dict_row; commits the call record even when the call fails."""
    k = KINDS[kind]
    s = current(conn)
    if provider is None:
        # Written by itself (after an import, or on Monday): the default provider, or the other one if only it
        # has a key.
        provider = s["default_provider"]
        other = next(p for p in NAMES if p != provider)
        if not ai_providers.configured(provider) and ai_providers.configured(other):
            provider = other
    model = s[f"{provider}_model"]
    built = k["build"](conn, ref_id)
    if built is None:
        raise ai_providers.Failed("what the report is about no longer exists")
    data, sha = built
    conn.commit()  # no transaction held open during the call, which can take a minute
    user = f"{k['intro']}:\n```json\n" + json.dumps(data, separators=(",", ":"), default=str) + "\n```\nWrite the report."
    t0 = time.monotonic()
    try:
        out = ai_providers.complete(provider, model, k["system"], user, MAX_OUTPUT_TOKENS)
    except (ai_providers.Temporary, ai_providers.Failed, ai_providers.NotConfigured) as e:
        conn.execute(
            "INSERT INTO ai_call (provider, model, purpose, ref_id, ok, ms, error) VALUES (%s, %s, %s, %s, false, %s, %s)",
            (provider, model, kind, ref_id, round((time.monotonic() - t0) * 1000), str(e)[:300]),
        )
        conn.commit()
        raise
    ms = round((time.monotonic() - t0) * 1000)
    text, removed = ai_guard.clean(out.text)
    if removed:
        log.warning("dose guard removed %d sentence(s) from the %s on %s", removed, kind, ref_id)
    conn.execute(
        "INSERT INTO ai_call (provider, model, purpose, ref_id, ok, input_tokens, output_tokens, ms) "
        "VALUES (%s, %s, %s, %s, true, %s, %s, %s)",
        (provider, out.model, kind, ref_id, out.input_tokens, out.output_tokens, ms),
    )
    r = conn.execute(
        """INSERT INTO ai_report (scope, ref_id, provider, model, prompt_version, input_sha, input_tokens, output_tokens, removed, text)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (k["scope"], ref_id, provider, out.model, k["version"], sha, out.input_tokens, out.output_tokens, removed, text),
    ).fetchone()
    conn.commit()
    return {"id": r["id"], "removed": removed}


def write_run_report(conn, activity_id: int, provider: str | None) -> dict:
    return write_report(conn, "run_report", activity_id, provider)


# --- the run page --------------------------------------------------------------------------------------


class ReportIn(BaseModel):
    provider: Literal["claude", "gemini"] | None = None


def _job(r):
    if r is None:
        return None
    return {k: (v.timestamp() if hasattr(v, "timestamp") else v) for k, v in r.items()}


def _state(conn, kind: str, ref_id: int) -> dict:
    k = KINDS[kind]
    r = conn.execute(
        "SELECT id, created_at, provider, model, prompt_version, input_sha, input_tokens, output_tokens, removed, text "
        "FROM ai_report WHERE scope = %s AND ref_id = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (k["scope"], ref_id),
    ).fetchone()
    job = conn.execute(
        "SELECT id, provider, trigger, requested_at, started_at, finished_at, attempts, next_try_at, ok, error "
        "FROM ai_job WHERE kind = %s AND ref_id = %s ORDER BY requested_at DESC, id DESC LIMIT 1",
        (kind, ref_id),
    ).fetchone()
    stale = False
    if r is not None:
        built = k["build"](conn, ref_id)
        stale = built is not None and built[1] != r["input_sha"]
    s = current(conn)
    report = None
    if r is not None:
        report = {**{x: v for x, v in r.items() if x != "input_sha"}, "created_at": r["created_at"].timestamp(), "stale": stale}
    return {
        "report": report,
        "job": _job(job),  # the latest: open (finished_at null) while it is being written
        "service": service(conn),
        "default_provider": s["default_provider"],
        "max_attempts": MAX_ATTEMPTS,
    }


def _request(conn, kind: str, ref_id: int, provider: str | None) -> dict:
    provider = provider or current(conn)["default_provider"]
    svc = service(conn)
    if not svc["running"]:
        raise HTTPException(status_code=503, detail="the analysis service is not running")
    if not svc["configured"][provider]:
        raise HTTPException(status_code=400, detail=f"no API key for {NAMES[provider]}: set {ai_providers.KEYS[provider]} in .env")
    if conn.execute("SELECT 1 FROM ai_job WHERE kind = %s AND ref_id = %s AND finished_at IS NULL", (kind, ref_id)).fetchone():
        raise HTTPException(status_code=409, detail="this report is already being written")
    return {"job": queue(conn, ref_id, provider, "request", kind)}


def _activity(conn, activity_id: int) -> None:
    if conn.execute("SELECT 1 FROM activity WHERE id = %s", (activity_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="activity not found")


def _week_ref(day: date) -> int:
    monday = week.monday_of(day)
    if monday > week.monday_of(datetime.now(LOCAL).date()):
        raise HTTPException(status_code=404, detail="that week has not started")
    return week.ref_of(monday)


@router.get("/activities/{activity_id}/report")
def get_report(activity_id: int):
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        _activity(conn, activity_id)
        return _state(conn, "run_report", activity_id)


@router.post("/activities/{activity_id}/report", status_code=202)
def request_report(activity_id: int, body: ReportIn):
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        _activity(conn, activity_id)
        return _request(conn, "run_report", activity_id, body.provider)


@router.get("/weeks/{day}/report")
def get_week_report(day: date):
    ref = _week_ref(day)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        return _state(conn, "week_summary", ref)


@router.post("/weeks/{day}/report", status_code=202)
def request_week_report(day: date, body: ReportIn):
    ref = _week_ref(day)
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        return _request(conn, "week_summary", ref, body.provider)

# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.30,<2", "httpx>=0.27,<1"]
# ///
"""Training Hub connector for the Claude desktop app (roadmap step 8, change 3).

A local MCP server: Claude Desktop starts it on the PC and talks to it over stdio; it reads the Training Hub
over HTTPS (on the LAN or through Tailscale) with a read-only API token. It can read; it cannot change
anything (the token is refused for anything but GET).

Run by Claude Desktop with uv, which installs the two dependencies above by itself:
  "command": "/usr/bin/uv", "args": ["run", "--script", "/home/you/traininghub/connector/traininghub_mcp.py"],
  "env": {"TRAININGHUB_URL": "https://training.example.com", "TRAININGHUB_TOKEN": "th_..."}
(see connector/README.md). Errors go to stderr, which Claude Desktop keeps in mcp-server-traininghub.log.
"""

import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request in Claude's log is noise

URL = os.environ.get("TRAININGHUB_URL", "https://training.example.com").rstrip("/")
TOKEN = os.environ.get("TRAININGHUB_TOKEN", "")
LOCAL = ZoneInfo("Europe/Madrid")

# Tests replace this with a client on the app itself.
client = httpx.Client(base_url=URL, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60.0)


class Problem(Exception):
    pass


def get(path: str, **params) -> dict | list:
    """GET from the Training Hub; a clear message instead of a stack trace when it fails."""
    params = {k: v for k, v in params.items() if v is not None}
    try:
        r = client.get(path, params=params)
    except httpx.TransportError as e:
        raise Problem(f"Training Hub not reachable at {URL} ({type(e).__name__}): is the PC on the LAN or on Tailscale?") from None
    if r.status_code == 401:
        raise Problem("Training Hub refused the token: make a new one (python -m app.api_tokens create) and put it in the config.")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise Problem(f"Training Hub answered {r.status_code}{f': {detail}' if isinstance(detail, str) else ''}")
    return r.json()


def _reference() -> str:
    try:
        return get("/api/connector/reference")["text"]
    except Exception as e:  # the server must start even when the Training Hub is down
        print(f"reference not loaded: {e}", file=sys.stderr)
        return "(The reference text could not be loaded from the Training Hub.)"


INSTRUCTIONS = """These tools read a Training Hub: Garmin activities, FreeStyle Libre 2 glucose (mg/dL), his \
insulin and carbs log, per-run numbers computed by code, weekly figures, and the AI reports the app wrote.

How to use them well:
- Start from list_activities or get_week to find what a question is about, then get_activity for one run, \
glucose_between / log_between for the details around it, similar_runs to compare starting states.
- Glucose is what this app is for. Whenever you talk about a session, give its glucose next to the training \
numbers (start, change, lowest, time below range), and call get_activity for any session you comment on: its \
flags come first in your answer (safety ones above all, such as a start above 270 mg/dL), and glucose is the first \
thing to look at when something is unusual (HR drift, a slow or hard run), before heat, sleep or fuelling.
- Every number comes from code: quote it, do not recompute it differently. Say how many runs a pattern rests on; \
under three is something to watch, not a pattern. Insulin and carbs are only what was logged: say "per the log".
- The app's rule, which also holds here: do not suggest insulin doses or changes to insulin settings (ratios, \
sensitivity, targets, basal, timing). If something looks worth raising with the endocrinologist, say "Review \
suggested" and give the data.

Reference used by the app's own reports:

"""

mcp = FastMCP("Training Hub", instructions=INSTRUCTIONS + _reference(), log_level="WARNING")


def _out(data) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), default=str)


@mcp.tool()
def list_activities(days: int = 90, sport: str | None = None) -> str:
    """Every activity of the last `days` days (oldest first; `count` is how many), each with its per-run numbers:
    glucose at the start and end, change, change per 10 min, lowest and when, minutes below the low, coverage,
    insulin on board and carbs per the log, time since the last meal, HR drift. Start times are local. Flags (safety
    points such as a start above 270 mg/dL) are not here: get_activity has them. sport: running, cycling,
    swimming... or all when left out."""
    data = get("/api/compare", days=days, sport=sport)
    runs = []
    for r in data["runs"]:
        r.pop("curve", None)  # the glucose curve: get_activity or glucose_between have it
        for k in ("end", "local_hour", "local_time"):
            r.pop(k, None)
        r["start"] = datetime.fromtimestamp(r["start"], LOCAL).strftime("%a %Y-%m-%d %H:%M")
        runs.append(r)
    # The app's all-time count per sport ("sports") is left out: read next to a short window it looks like the
    # window's count, and the list then seems incomplete.
    return _out({"days": days, "sport": sport or "all", "count": len(runs), "activities": runs})


@mcp.tool()
def get_activity(activity_id: int) -> str:
    """One activity in full, as the app's run report sees it: summary, laps, zones, glucose and context numbers,
    flags, the note, what was logged from 6 h before to 2 h after, glucose from 1 h before to 2 h after, heart rate,
    pace and altitude in 30 s means, the 5 most similar past runs, and the app's newest report on it."""
    return _out(get(f"/api/connector/activity/{activity_id}"))


@mcp.tool()
def get_week(day: str) -> str:
    """The training week (Monday to Sunday) holding `day` (YYYY-MM-DD): load per sport and zone, every session,
    glucose figures against the time-in-range targets, nights after training, each day's log totals, flags, and
    the app's weekly summary."""
    data = get(f"/api/weeks/{day}")
    report = get(f"/api/weeks/{day}/report").get("report")
    data["summary"] = None if not report else report["text"]
    return _out(data)


@mcp.tool()
def similar_runs(glucose: int | None = None, trend: int | None = None, iob: float | None = None,
                 minutes_since_meal: int | None = None, sport: str | None = "running") -> str:
    """Past activities that started closest to a state, and what happened in them. Any of: glucose (mg/dL), trend
    (1 falling fast, 2 falling, 3 stable, 4 rising, 5 rising fast), iob (rapid insulin on board, u),
    minutes_since_meal. History, not a prediction."""
    return _out(get("/api/compare/similar", glucose=glucose, trend=trend, iob=iob, since_meal=minutes_since_meal, sport=sport))


@mcp.tool()
def glucose_between(start: str, end: str) -> str:
    """Glucose between two times (ISO 8601; a bare date is local midnight; at most 14 days), with time in range,
    below and above, the mean and coverage. Up to 12 h: every reading; longer: 15-minute buckets."""
    return _out(get("/api/connector/glucose", start=start, end=end))


@mcp.tool()
def log_between(start: str, end: str) -> str:
    """Insulin doses and carbs logged between two times (ISO 8601; at most 62 days), meals with their foods."""
    return _out(get("/api/connector/log", start=start, end=end))


@mcp.tool()
def current_state() -> str:
    """Now: the latest glucose and trend (if under 15 min old), rapid insulin on board and minutes since the last
    carbs, per the log."""
    return _out(get("/api/compare/now"))


@mcp.tool()
def training_settings() -> str:
    """Heart-rate zones, LTHR, max and resting HR, the glucose target range and the sensor lag setting."""
    return _out(get("/api/settings/training")["current"])


if __name__ == "__main__":
    if not TOKEN:
        print("TRAININGHUB_TOKEN is not set in the connector's config", file=sys.stderr)
    mcp.run()

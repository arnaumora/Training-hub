"""Garmin sync service (roadmap step 6). Runs as its own container: python -m app.garmin_sync

When it syncs:
  - at SYNC_TIMES local time (13:00 and 22:00, Europe/Madrid)
  - when "Sync now" queues a run (a garmin_sync_run row with started_at NULL; checked every 30 s)
  - at start-up, when no run succeeded in the last 12 hours (so a restart or a deploy catches up)
  - once more 30 min after a run that failed for a temporary reason (network, Garmin down, rate limit)

How it logs in: only with the tokens saved by the login command (python -m app.garmin_login). The library
is created WITHOUT credentials, so a missing or rejected token makes the run stop with login_needed;
the library never attempts a password login and never deletes the token file in that case (checked in
garminconnect 0.3.17, Garmin.login). Refreshed tokens are written back to the same file by the library.
A login_needed run is not retried automatically: it waits for the login command, which queues a run.

What it does: list the activities from (newest synced activity - OVERLAP_DAYS), or from BACKFILL_DAYS ago on
the first run, download the original file of each one not seen before, and import it with exactly the same
code as a manual upload (app.fit_import.import_bytes: raw archive first, then parse). One transaction per
activity, so a failure halfway keeps what was already imported.
"""

import logging
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from . import derive, fit_import
from .common import conninfo

log = logging.getLogger("traininghub.garmin")

LOCAL = ZoneInfo("Europe/Madrid")
SYNC_TIMES = ((13, 0), (22, 0))
POLL_S = 30
STARTUP_IF_NO_SUCCESS_H = 12
RETRY_AFTER_S = 30 * 60
OVERLAP_DAYS = 2
DOWNLOAD_PAUSE_S = 2.0
TOKEN_FILE = "garmin_tokens.json"  # the file garminconnect 0.3 writes inside the token folder
HEARTBEAT = Path("/tmp/heartbeat")


def token_dir() -> str:
    return os.environ.get("GARMIN_TOKENS", "/garmin")


def backfill_days() -> int:
    return int(os.environ.get("GARMIN_BACKFILL_DAYS", "30"))


class LoginNeeded(Exception):
    """The saved tokens are missing or Garmin rejected them: run the login command."""


class TemporaryProblem(Exception):
    """Worth one retry later: network, Garmin errors, rate limiting."""


# --- schedule --------------------------------------------------------------------------------------


def next_sync_time(now: datetime) -> datetime:
    """The first SYNC_TIMES moment strictly after `now` (aware), in UTC. Daylight saving time is handled by
    building the local wall-clock time for each day and converting it."""
    local = now.astimezone(LOCAL)
    for day in range(0, 3):
        d = local.date() + timedelta(days=day)
        for h, m in SYNC_TIMES:
            t = datetime(d.year, d.month, d.day, h, m, tzinfo=LOCAL)
            if t > local:
                return t.astimezone(UTC)
    raise AssertionError("unreachable")


# --- Garmin ----------------------------------------------------------------------------------------


def connect():
    """A logged-in garminconnect client from the saved tokens only. Raises LoginNeeded or TemporaryProblem."""
    from garminconnect import (  # imported here: only this service needs the library
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )

    folder = token_dir()
    if not (Path(folder) / TOKEN_FILE).is_file():
        raise LoginNeeded("no Garmin tokens saved yet: run the login command on the server")
    client = Garmin(retry_attempts=2)  # no email or password: never a credential login from here
    try:
        client.login(folder)
    except GarminConnectAuthenticationError as e:
        raise LoginNeeded(f"Garmin rejected the saved tokens: run the login command again ({_short(e)})") from e
    except GarminConnectTooManyRequestsError as e:
        raise TemporaryProblem(f"Garmin is rate limiting ({_short(e)})") from e
    except GarminConnectConnectionError as e:
        raise TemporaryProblem(f"could not reach Garmin ({_short(e)})") from e
    return client


def _short(e: Exception) -> str:
    return " ".join(str(e).split())[:200]


def download_original(client, activity_id: int) -> bytes:
    from garminconnect import Garmin

    return client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)


def _is_not_found(e: Exception) -> bool:
    try:
        from garminconnect import GarminConnectNotFoundError
    except ImportError:  # pragma: no cover
        return False
    return isinstance(e, GarminConnectNotFoundError)


# --- one run ---------------------------------------------------------------------------------------


def _connect_db():
    return psycopg.connect(conninfo(), row_factory=dict_row)


def sync_window_start(conn, today: date) -> date:
    r = conn.execute("SELECT max(start_ts) AS newest FROM activity WHERE garmin_activity_id IS NOT NULL").fetchone()
    if r["newest"] is None:
        return today - timedelta(days=backfill_days())
    return r["newest"].astimezone(LOCAL).date() - timedelta(days=OVERLAP_DAYS)


def run_sync(client_factory=connect, downloader=download_original, pause_s: float = DOWNLOAD_PAUSE_S) -> dict:
    """List, download and import new activities. Returns counts. Raises LoginNeeded / TemporaryProblem."""
    today = datetime.now(LOCAL).date()
    with _connect_db() as conn:
        start = sync_window_start(conn, today)
    client = client_factory()
    try:
        listed = client.get_activities_by_date(start.isoformat(), (today + timedelta(days=1)).isoformat(), sortorder="asc")
    except Exception as e:
        raise TemporaryProblem(f"could not list activities ({_short(e)})") from e

    with _connect_db() as conn:
        seen = {r["garmin_activity_id"] for r in conn.execute("SELECT garmin_activity_id FROM garmin_activity_seen")}
        seen |= {r["garmin_activity_id"] for r in conn.execute("SELECT garmin_activity_id FROM activity WHERE garmin_activity_id IS NOT NULL")}

    counts = {"checked": len(listed), "imported": 0, "skipped": 0}
    for item in listed:
        aid = item.get("activityId")
        if not isinstance(aid, int) or aid in seen:
            continue
        try:
            data = downloader(client, aid)
        except Exception as e:
            if _is_not_found(e):  # typed in by hand on Garmin Connect: there is no original file
                _mark_seen(aid, "skipped", "no original file on Garmin Connect")
                counts["skipped"] += 1
                continue
            raise TemporaryProblem(f"could not download activity {aid} ({_short(e)})") from e
        with _connect_db() as conn:
            try:
                result = fit_import.import_bytes(conn, data, source="garmin", garmin_activity_id=aid)
                status, detail = "imported", None
                # A file already uploaded by hand is linked to its Garmin id, not added: not "new".
                counts["imported"] += 0 if result["duplicate"] else 1
            except fit_import.FitProblem as e:
                conn.rollback()
                status, detail = "skipped", str(e)
                counts["skipped"] += 1
            # OSError (raw archive not writable) is not caught: the run fails and nothing is marked seen.
            conn.execute(
                "INSERT INTO garmin_activity_seen (garmin_activity_id, status, detail) VALUES (%s, %s, %s) "
                "ON CONFLICT (garmin_activity_id) DO UPDATE SET status = EXCLUDED.status, detail = EXCLUDED.detail, seen_at = now()",
                (aid, status, detail),
            )
        seen.add(aid)
        if pause_s:
            time.sleep(pause_s)
    return counts


def _mark_seen(aid: int, status: str, detail: str | None) -> None:
    with _connect_db() as conn:
        conn.execute(
            "INSERT INTO garmin_activity_seen (garmin_activity_id, status, detail) VALUES (%s, %s, %s) "
            "ON CONFLICT (garmin_activity_id) DO NOTHING",
            (aid, status, detail),
        )


def execute(run_ids: list[int], **kwargs) -> bool | None:
    """Run one sync for the given (already claimed) run rows and record the outcome on them and in
    collector_status. Returns True when it succeeded; False after a temporary problem; None when a login
    is needed (no automatic retry)."""
    ok, login_needed, error, counts = False, False, None, {"checked": None, "imported": None, "skipped": None}
    try:
        counts = run_sync(**kwargs)
        ok = True
    except LoginNeeded as e:
        login_needed, error = True, f"login needed: {e}"
    except TemporaryProblem as e:
        error = str(e)
    except Exception as e:  # anything unexpected: recorded, the service keeps running
        log.exception("Garmin sync failed")
        error = f"unexpected error: {_short(e)}"
    with _connect_db() as conn:
        conn.execute(
            "UPDATE garmin_sync_run SET finished_at = now(), ok = %s, login_needed = %s, checked = %s, imported = %s, "
            "skipped = %s, error = %s WHERE id = ANY(%s)",
            (ok, login_needed, counts["checked"], counts["imported"], counts["skipped"], error, run_ids),
        )
        if ok:
            conn.execute(
                "INSERT INTO collector_status (name, last_attempt_at, last_success_at, consecutive_failures) "
                "VALUES ('garmin', now(), now(), 0) ON CONFLICT (name) DO UPDATE SET last_attempt_at = now(), "
                "last_success_at = now(), consecutive_failures = 0"
            )
        else:
            conn.execute(
                "INSERT INTO collector_status (name, last_attempt_at, last_error_at, last_error, consecutive_failures) "
                "VALUES ('garmin', now(), now(), %s, 1) ON CONFLICT (name) DO UPDATE SET last_attempt_at = now(), "
                "last_error_at = now(), last_error = EXCLUDED.last_error, "
                "consecutive_failures = collector_status.consecutive_failures + 1",
                (error[:300],),
            )
    if ok:
        log.info("sync done: %s", counts)
        return True
    log.warning("sync failed: %s", error)
    return None if login_needed else False


def claim(trigger: str) -> list[int]:
    """Claim the queued requests (all of them: one run serves them), or create a row for `trigger`."""
    with _connect_db() as conn:
        if trigger == "request":
            rows = conn.execute(
                "UPDATE garmin_sync_run SET started_at = now() WHERE started_at IS NULL RETURNING id"
            ).fetchall()
            return [r["id"] for r in rows]
        return [conn.execute(
            "INSERT INTO garmin_sync_run (trigger, started_at) VALUES (%s, now()) RETURNING id", (trigger,)
        ).fetchone()["id"]]


def has_pending() -> bool:
    with _connect_db() as conn:
        return conn.execute("SELECT EXISTS (SELECT 1 FROM garmin_sync_run WHERE started_at IS NULL) AS p").fetchone()["p"]


def recent_success() -> bool:
    with _connect_db() as conn:
        r = conn.execute(
            "SELECT last_success_at > now() - make_interval(hours => %s) AS fresh FROM collector_status WHERE name = 'garmin'",
            (STARTUP_IF_NO_SUCCESS_H,),
        ).fetchone()
    return bool(r and r["fresh"])


# --- the loop --------------------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("garminconnect").setLevel(logging.WARNING)
    while True:  # the database may still be starting
        try:
            started_fresh = not recent_success()
            break
        except psycopg.OperationalError:
            log.info("waiting for the database")
            time.sleep(5)
    next_due = next_sync_time(datetime.now(UTC))
    retry_at = None
    next_derive = 0.0  # the per-run numbers: refreshed at start-up, then every derive.REFRESH_EVERY_S
    log.info("Garmin sync service: tokens in %s, next scheduled run %s", token_dir(), next_due.astimezone(LOCAL).isoformat())
    if started_fresh:
        result = execute(claim("startup"))
        retry_at = time.monotonic() + RETRY_AFTER_S if result is False else None
    while True:
        HEARTBEAT.touch()
        try:
            now = datetime.now(UTC)
            if has_pending():
                result = execute(claim("request"))
            elif now >= next_due:
                next_due = next_sync_time(now)
                result = execute(claim("schedule"))
            elif retry_at is not None and time.monotonic() >= retry_at:
                retry_at = None
                result = execute(claim("retry"))
            else:
                result = "idle"
            if result is False:
                retry_at = time.monotonic() + RETRY_AFTER_S
            elif result is True or result is None:
                retry_at = None
            if time.monotonic() >= next_derive:
                next_derive = time.monotonic() + derive.REFRESH_EVERY_S
                n = derive.refresh()
                if n:
                    log.info("per-run numbers recomputed for %d activities", n)
        except psycopg.OperationalError as e:
            log.warning("database unreachable: %s", e)
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()

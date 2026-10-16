"""Collector loop: one LibreLinkUp request per minute, forever.

Each poll stores the current reading (source llu_live) and backfills any missing 15-minute
history points (source llu_graph). Existing rows always win, so a live reading is never
replaced by a coarse one. Every response is archived raw before parsing.

Failure handling:
  - 401 from LibreLinkUp: log in again on the next poll.
  - 429: wait as long as LibreLinkUp asks (default 5 min).
  - anything else: back off 1, 2, 4 ... up to 15 min, and record the error in collector_status.
A heartbeat file is touched after every successful poll; the container healthcheck reads it.
"""

import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests
from pylibrelinkup.exceptions import LLUAPIRateLimitError

from . import store
from .archive import archive_response
from .llu import Session
from .parse import parse_graph

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
MAX_BACKOFF_SECONDS = 15 * 60
RAW_ROOT = Path(os.environ.get("RAW_DIR", "/raw"))
SECRETS = Path(os.environ.get("SECRETS_DIR", "/run/secrets"))
HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))

log = logging.getLogger("collector")


def read_secret(name: str) -> str:
    value = (SECRETS / name).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"secret {name} is empty")
    return value


def record_failure_safely(error: str) -> None:
    """Record a failure without letting a database problem crash the loop."""
    try:
        with store.connect() as conn:
            store.record_failure(conn, error)
    except Exception:
        log.warning("could not record failure in collector_status (database unreachable?)")


def poll_once(session: Session) -> None:
    raw = session.fetch_graph()
    archive_response(RAW_ROOT, raw)
    current, history = parse_graph(raw)
    with store.connect() as conn:
        live_added = store.insert_readings(conn, [current] if current else [], "llu_live")
        coarse_added = store.insert_readings(conn, history, "llu_graph")
        store.record_success(conn)
    if live_added and current:
        age = int((datetime.now(UTC) - current.ts).total_seconds())
        log.info("new live reading for %s UTC (age %d s)", current.minute.strftime("%Y-%m-%d %H:%M"), age)
    if coarse_added:
        log.info("backfilled %d coarse history point(s)", coarse_added)
    HEARTBEAT.touch()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    email, password = read_secret("llu_email"), read_secret("llu_password")
    log.info("starting: poll every %d s, raw archive in %s", POLL_SECONDS, RAW_ROOT)

    session: Session | None = None
    backoff = POLL_SECONDS
    while True:
        started = time.monotonic()
        try:
            if session is None:
                session = Session(email, password)
            poll_once(session)
            backoff = POLL_SECONDS
            time.sleep(max(0.0, POLL_SECONDS - (time.monotonic() - started)))
        except LLUAPIRateLimitError as e:
            wait = e.retry_after or 300
            log.warning("rate limited by LibreLinkUp, waiting %d s", wait)
            record_failure_safely(f"rate limited, retry after {wait} s")
            time.sleep(wait)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code == 401:
                log.warning("LibreLinkUp session expired, logging in again")
                session = None
            else:
                log.warning("LibreLinkUp HTTP error %s, retrying in %d s", code, backoff)
            record_failure_safely(f"HTTP {code}")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
        except Exception as e:  # network errors, timeouts, database down, unexpected responses
            log.exception("poll failed, retrying in %d s", backoff)
            record_failure_safely(f"{type(e).__name__}: {e}")
            if isinstance(e, (requests.ConnectionError, requests.Timeout)):
                session = None
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    sys.exit(main())

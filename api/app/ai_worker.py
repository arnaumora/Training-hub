"""The analysis service (roadmap step 8): works through ai_job, one job at a time.

python -m app.ai_worker   (the `analysis` service in compose.yaml, same image as the API)

A job is claimed with FOR UPDATE SKIP LOCKED and marked started; a provider call takes seconds to a minute.
  - temporary failure (timeout, 429, 5xx): tried again after 2, 4 then 8 minutes, at most MAX_ATTEMPTS times
  - lasting failure (no key, bad request, refusal, the activity is gone): finished with its error
Once a minute it also queues the weekly summary when one is due (app.ai_reports.queue_weekly).
Jobs left half-done by a restart are picked up again at start-up. A heartbeat file feeds the container
health check. Keys and report texts are never logged.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from . import ai_providers, ai_reports
from .common import conninfo

log = logging.getLogger("traininghub.ai_worker")

POLL_S = 5
STATUS_EVERY_S = 60
MAX_ATTEMPTS = ai_reports.MAX_ATTEMPTS
HEARTBEAT = Path("/tmp/analysis-heartbeat")


def claim(conn) -> dict | None:
    with conn.transaction():
        job = conn.execute(
            """SELECT id, kind, ref_id, provider, attempts FROM ai_job
               WHERE finished_at IS NULL AND started_at IS NULL AND (next_try_at IS NULL OR next_try_at <= now())
               ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED"""
        ).fetchone()
        if job:
            conn.execute("UPDATE ai_job SET started_at = now(), attempts = attempts + 1 WHERE id = %s", (job["id"],))
    return job


def report_status(conn) -> None:
    """Tell the API that this service is alive and which providers have a key (the API has no keys)."""
    conn.execute(
        """INSERT INTO ai_service (id, seen_at, claude, gemini) VALUES (1, now(), %s, %s)
           ON CONFLICT (id) DO UPDATE SET seen_at = excluded.seen_at, claude = excluded.claude, gemini = excluded.gemini""",
        (ai_providers.configured("claude"), ai_providers.configured("gemini")),
    )
    conn.commit()


def run_one(conn) -> bool:
    """Claim and do one job. False when there was nothing to do."""
    job = claim(conn)
    if job is None:
        return False
    attempt = job["attempts"] + 1
    try:
        out = ai_reports.write_report(conn, job["kind"], job["ref_id"], job["provider"])
        conn.execute("UPDATE ai_job SET finished_at = now(), ok = true, error = NULL WHERE id = %s", (job["id"],))
        log.info("%s written for %s (job %s%s)", job["kind"], job["ref_id"], job["id"],
                 f", {out['removed']} sentence(s) removed by the dose guard" if out["removed"] else "")
    except ai_providers.Temporary as e:
        if attempt < MAX_ATTEMPTS:
            wait = timedelta(minutes=2 ** attempt)
            conn.execute(
                "UPDATE ai_job SET started_at = NULL, next_try_at = %s, error = %s WHERE id = %s",
                (datetime.now(UTC) + wait, f"{e} (trying again)", job["id"]),
            )
            log.warning("job %s: %s, trying again in %s", job["id"], e, wait)
        else:
            conn.execute("UPDATE ai_job SET finished_at = now(), ok = false, error = %s WHERE id = %s", (str(e)[:300], job["id"]))
            log.warning("job %s failed after %d attempts: %s", job["id"], attempt, e)
    except (ai_providers.Failed, ai_providers.NotConfigured) as e:
        conn.execute("UPDATE ai_job SET finished_at = now(), ok = false, error = %s WHERE id = %s", (str(e)[:300], job["id"]))
        log.warning("job %s failed: %s", job["id"], e)
    except Exception:  # a bug: record it, keep the service alive
        conn.rollback()
        conn.execute("UPDATE ai_job SET finished_at = now(), ok = false, error = 'internal error' WHERE id = %s", (job["id"],))
        log.exception("job %s: internal error", job["id"])
    conn.commit()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines would show URLs, never needed
    while True:
        try:
            with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
                n = conn.execute(
                    "UPDATE ai_job SET started_at = NULL WHERE finished_at IS NULL AND started_at IS NOT NULL"
                ).rowcount
                conn.commit()
                if n:
                    log.info("%d unfinished job(s) from before a restart queued again", n)
                log.info("analysis service ready (keys set: %s)",
                         ", ".join(p for p in ai_reports.NAMES if ai_providers.configured(p)) or "none")
                next_status = 0.0
                while True:
                    HEARTBEAT.touch()
                    if time.monotonic() >= next_status:
                        report_status(conn)
                        if ai_reports.queue_weekly(conn):
                            log.info("weekly summary queued")
                        next_status = time.monotonic() + STATUS_EVERY_S
                    if not run_one(conn):
                        time.sleep(POLL_S)
        except psycopg.OperationalError as e:
            log.warning("database unreachable: %s", e)
            time.sleep(POLL_S)


if __name__ == "__main__":
    main()

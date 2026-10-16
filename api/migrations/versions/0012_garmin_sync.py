"""Garmin sync bookkeeping (roadmap step 6)

garmin_sync_run: one row per sync run, and the queue for "Sync now": the API inserts a row with
started_at NULL and the garmin service claims it. trigger says why it ran (schedule, request, startup,
retry). login_needed marks a run that stopped because the saved Garmin tokens were missing or rejected.

garmin_activity_seen: every Garmin activity id the sync has dealt with, imported or skipped (for example
an activity typed in by hand on Garmin Connect has no file), so it is not downloaded again on every run.

The garmin service also keeps a row named 'garmin' in collector_status, like the glucose collector.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE garmin_sync_run (
            id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            trigger      text        NOT NULL CHECK (trigger IN ('schedule', 'request', 'startup', 'retry')),
            requested_at timestamptz NOT NULL DEFAULT now(),
            started_at   timestamptz,
            finished_at  timestamptz,
            ok           boolean,
            login_needed boolean     NOT NULL DEFAULT false,
            checked      integer,
            imported     integer,
            skipped      integer,
            error        text,
            CHECK (finished_at IS NULL OR started_at IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX garmin_sync_run_pending ON garmin_sync_run (requested_at) WHERE started_at IS NULL")
    op.execute(
        """
        CREATE TABLE garmin_activity_seen (
            garmin_activity_id bigint      PRIMARY KEY,
            status             text        NOT NULL CHECK (status IN ('imported', 'skipped')),
            detail             text,
            seen_at            timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE garmin_activity_seen")
    op.execute("DROP TABLE garmin_sync_run")

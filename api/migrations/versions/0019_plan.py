"""Training plan import (roadmap step 9, change 1): the sessions of the plan's .ics calendars.

planned_session holds one row per event (VEVENT), keyed by its UID: importing a calendar again updates its
sessions in place, and a session no longer in the file is marked removed (removed_at), never deleted, so a
later change can keep what was linked to it. plan_import records every import with the raw file it came
from (archived under RAW_DIR/plan/ before parsing), so the sessions can be rebuilt from the raw files.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-13
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE plan_import (
            id          integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            calendar    text         NOT NULL,
            imported_at timestamptz  NOT NULL DEFAULT now(),
            raw_path    text         NOT NULL,
            sha256      text         NOT NULL,
            added       integer      NOT NULL,
            updated     integer      NOT NULL,
            unchanged   integer      NOT NULL,
            removed     integer      NOT NULL
        );
        CREATE INDEX plan_import_calendar ON plan_import (calendar, imported_at DESC);

        CREATE TABLE planned_session (
            id          integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            calendar    text         NOT NULL,
            uid         text         NOT NULL UNIQUE,
            day         date         NOT NULL,
            start_ts    timestamptz,
            end_ts      timestamptz,
            sport       text         NOT NULL CHECK (sport IN ('running', 'swimming', 'cycling', 'strength', 'rest', 'other')),
            title       text         NOT NULL,
            description text,
            planned_m   numeric(8, 1) CHECK (planned_m > 0),
            created_at  timestamptz  NOT NULL DEFAULT now(),
            updated_at  timestamptz  NOT NULL DEFAULT now(),
            removed_at  timestamptz
        );
        CREATE INDEX planned_session_day ON planned_session (day) WHERE removed_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE planned_session; DROP TABLE plan_import")
